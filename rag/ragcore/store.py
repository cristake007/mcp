"""Standalone PostgreSQL store, incremental ingest and hybrid retrieval."""
import hashlib
import os
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .articles import article_by_label, article_for_hit
from .chunking import make_chunks, verify_coverage
from .embeddings import MODEL_ID, Embedder, cached_embedder
from .parsing import SUPPORTED, parse_file
from .ocr import OCR_VERSION, enabled as ocr_enabled, extract_pdf_figures


def connect():
    # libpq reads PGHOST, PGDATABASE, PGUSER and PGPASSWORD from Compose.
    return psycopg.connect(autocommit=True, row_factory=dict_row, connect_timeout=10)


def init_schema(conn) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(schema)


def walk_documents(folder: Path):
    root = folder.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Documents path is not a directory: {folder}")

    def fail(error):
        raise error

    for directory, directories, files in os.walk(root, followlinks=False, onerror=fail):
        directories[:] = sorted(name for name in directories
                                if not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"File escapes document root: {path}")
            yield path.relative_to(root).as_posix(), path


def file_digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def scan(conn, folder: Path, *, use_vectors: bool = True,
         prune: bool = False, only_paths: set[str] | None = None) -> dict:
    """Serialize all manual and automatic RAG scans with a PostgreSQL advisory lock."""
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(8460153001) AS acquired")
        if not cur.fetchone()["acquired"]:
            raise RuntimeError("Another RAG indexing process is running; retry later")
    try:
        return _scan_locked(conn, folder, use_vectors=use_vectors,
                            prune=prune, only_paths=only_paths)
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(8460153001)")


def _scan_locked(conn, folder: Path, *, use_vectors: bool,
                 prune: bool, only_paths: set[str] | None) -> dict:
    init_schema(conn)
    candidates = list(walk_documents(folder))
    seen = {name for name, _ in candidates}
    embedding_model = MODEL_ID if use_vectors else "none"
    embedder = None
    numbers = {"updated": 0, "unchanged": 0, "errors": 0, "deleted": 0, "empty": 0}
    for relative, path in candidates:
        if only_paths is not None and relative not in only_paths:
            continue
        try:
            initial_stat = path.stat()
            if initial_stat.st_size > 30 * 1024 * 1024:
                raise ValueError("File larger than 30 MB limit")
            signature = file_digest(path)
            requested_ocr_version = (OCR_VERSION if path.suffix.lower() == ".pdf"
                                     and ocr_enabled() else 0)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sha256, embedding_model, ocr_version FROM rag_documents WHERE path=%s",
                    (relative,),
                )
                previous = cur.fetchone()
            if (previous and previous["sha256"] == signature
                    and previous["embedding_model"] == embedding_model
                    and previous["ocr_version"] == requested_ocr_version):
                numbers["unchanged"] += 1
                continue

            parsed = parse_file(path)
            figures, ocr_warnings = (
                extract_pdf_figures(path) if requested_ocr_version else ([], [])
            )
            parsed.warnings.extend(ocr_warnings)
            chunks = make_chunks(parsed.raw_text, parsed.units)
            verify_coverage(parsed.raw_text, chunks)
            if chunks and use_vectors:
                if embedder is None:
                    embedder = cached_embedder()
                vectors = embedder.embed([item.text for item in chunks])
            else:
                vectors = [None] * len(chunks)
            ocr_texts = [
                (figure["caption"] + "\n" + figure["ocr_text"]).strip()
                for figure in figures
            ]
            if use_vectors and ocr_texts:
                if embedder is None:
                    embedder = cached_embedder()
                ocr_vectors = embedder.embed(ocr_texts)
            else:
                ocr_vectors = [None] * len(figures)

            # Detect writes happening while parsing or embedding. Keep the
            # previous indexed revision, and retry the next watcher cycle.
            final_stat = path.stat()
            if ((initial_stat.st_size, initial_stat.st_mtime_ns) !=
                    (final_stat.st_size, final_stat.st_mtime_ns) or
                    file_digest(path) != signature):
                raise RuntimeError("File changed while indexing; retry when stable")

            # One transaction per file: parser/model/DB errors leave its previous
            # indexed version intact. The original document is read-only.
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO rag_documents
                        (path, sha256, parser, raw_text, warnings, embedding_model, ocr_version)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (path) DO UPDATE SET
                          sha256=EXCLUDED.sha256, parser=EXCLUDED.parser,
                          raw_text=EXCLUDED.raw_text, warnings=EXCLUDED.warnings,
                          embedding_model=EXCLUDED.embedding_model,
                          ocr_version=EXCLUDED.ocr_version, indexed_at=now()
                        RETURNING id""",
                        (relative, signature, parsed.parser, parsed.raw_text,
                         Jsonb(parsed.warnings), embedding_model, requested_ocr_version),
                    )
                    doc_id = cur.fetchone()["id"]
                    # Replacing a document revision is atomic for text AND OCR.
                    cur.execute("DELETE FROM rag_figures WHERE document_id=%s", (doc_id,))
                    cur.execute("DELETE FROM rag_chunks WHERE document_id=%s", (doc_id,))
                    for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                        cur.execute(
                            """INSERT INTO rag_chunks
                            (document_id, ordinal, char_start, char_end, content, locations, embedding)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::vector)""",
                            (doc_id, ordinal, chunk.start, chunk.end, chunk.text,
                             Jsonb(chunk.locations), vector),
                        )
                    for figure, ocr_content, vector in zip(
                        figures, ocr_texts, ocr_vectors, strict=True
                    ):
                        cur.execute(
                            """INSERT INTO rag_figures
                               (document_id, page, ordinal, kind, bbox, caption)
                               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (doc_id, figure["page"], figure["ordinal"],
                             figure["kind"], Jsonb(figure["bbox"]), figure["caption"]),
                        )
                        figure_id = cur.fetchone()["id"]
                        if ocr_content:
                            cur.execute(
                                """INSERT INTO rag_ocr_chunks
                                   (document_id, figure_id, page, content, ocr_text,
                                    bbox, confidence, embedding)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::vector)""",
                                (doc_id, figure_id, figure["page"], ocr_content,
                                 figure["ocr_text"], Jsonb(figure["ocr_bbox"]),
                                 figure["ocr_confidence"], vector),
                            )
            numbers["updated"] += 1
            if not chunks:
                numbers["empty"] += 1
            print(f"INDEXED {relative}: {len(chunks)} text chunks; "
                  f"figures={len(figures)}, ocr_chunks={len(ocr_texts)}; "
                  f"warnings={len(parsed.warnings)}", flush=True)
            for warning in parsed.warnings:
                print(f"  WARNING {relative}: {warning}", flush=True)
        except Exception as exc:
            numbers["errors"] += 1
            print(f"ERROR {relative}: {type(exc).__name__}: {exc}", flush=True)

    # Pruning is explicitly requested so a wrongly mounted empty directory
    # cannot silently wipe the existing index. Errors disable pruning.
    if prune:
        if numbers["errors"]:
            print("WARNING: skipping prune because scan reported errors", flush=True)
        elif not candidates:
            print("WARNING: refusing to prune from an empty documents directory", flush=True)
        else:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT id,path FROM rag_documents")
                    for row in cur.fetchall():
                        if row["path"] not in seen:
                            cur.execute("DELETE FROM rag_documents WHERE id=%s", (row["id"],))
                            numbers["deleted"] += 1
    return numbers


def search(conn, query: str, *, limit: int = 8, use_vectors: bool = True) -> list[dict]:
    """Weighted RRF: literal substring, strict Romanian FTS, forgiving OR FTS, vector."""
    init_schema(conn)
    query = query.strip()
    if not query or len(query) > 1000:
        raise ValueError("Query must contain 1-1000 characters")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")

    # Strip common Romanian conversational words from the forgiving lexical
    # branch. Keep the strict query separately for precise legal citations.
    words = re.findall(r"[^\W_]+", query.lower(), re.UNICODE)
    stop = {
        "care", "ce", "cum", "este", "sunt", "pentru", "despre", "din", "de", "la",
        "cu", "și", "si", "sau", "ale", "al", "a", "în", "in", "pe", "un", "o",
        "se", "sa", "să", "privind", "trebuie", "pot", "poate", "acest", "aceasta"
    }
    terms = list(dict.fromkeys(word for word in words if word not in stop and len(word) >= 2))
    or_query = " | ".join(terms[:12])

    rankings: list[tuple[float, list[int]]] = []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM rag_chunks
            WHERE position(lower(%s) in lower(content)) > 0
            ORDER BY length(content), id LIMIT 40""", (query,),
        )
        rankings.append((2.0, [row["id"] for row in cur.fetchall()]))
        cur.execute(
            """SELECT id FROM rag_chunks
            WHERE tsv @@ websearch_to_tsquery('romanian', %s)
            ORDER BY ts_rank_cd(tsv, websearch_to_tsquery('romanian', %s)) DESC, id
            LIMIT 40""", (query, query),
        )
        rankings.append((1.6, [row["id"] for row in cur.fetchall()]))
        if or_query:
            # Avoid one-word false positives, e.g. 'ISO 9001' must not
            # return documents that only contain 'ISO 14001'.
            minimum_match = 2 if len(terms) >= 2 else 1
            match_count_sql = " + ".join(
                "(tsv @@ plainto_tsquery('romanian', %s))::int"
                for _ in terms[:12]
            )
            cur.execute(
                f"""SELECT id FROM rag_chunks
                WHERE tsv @@ to_tsquery('romanian', %s)
                  AND ({match_count_sql}) >= %s
                ORDER BY ts_rank_cd(tsv, to_tsquery('romanian', %s)) DESC, id
                LIMIT 40""",
                (or_query, *terms[:12], minimum_match, or_query),
            )
            rankings.append((0.8, [row["id"] for row in cur.fetchall()]))
        if use_vectors:
            vector = cached_embedder().embed([query], is_query=True)[0]
            cur.execute(
                """SELECT c.id FROM rag_chunks c
                JOIN rag_documents d ON d.id=c.document_id
                WHERE c.embedding IS NOT NULL AND d.embedding_model=%s
                ORDER BY c.embedding <=> %s::vector LIMIT 40""",
                (MODEL_ID, vector),
            )
            rankings.append((1.0, [row["id"] for row in cur.fetchall()]))

        scores: dict[int, float] = {}
        for weight, ranking in rankings:
            for position, chunk_id in enumerate(ranking, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0) + weight / (60 + position)
        top = sorted(scores, key=lambda item: (-scores[item], item))[:limit]
        if not top:
            return []
        cur.execute(
            """SELECT c.id, c.document_id, d.path, d.raw_text,
                      c.char_start, c.char_end, c.content, c.locations
            FROM rag_chunks c JOIN rag_documents d ON d.id=c.document_id
            WHERE c.id=ANY(%s)""", (top,),
        )
        rows = {row["id"]: row for row in cur.fetchall()}
        output = []
        for chunk_id in top:
            record = rows[chunk_id]
            raw = record.pop("raw_text")
            article = article_for_hit(raw, record["char_start"], record["char_end"], query)
            output.append({
                **record,
                "article": article.info(include_text=raw) if article else None,
                "rrf_score": scores[chunk_id],
            })
        return output


def get_text(conn, document_id: int, start: int | None = None,
             end: int | None = None, *, article: str | None = None) -> tuple[str, str]:
    """Return literal extracted text, optionally by legal article label."""
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT path,raw_text FROM rag_documents WHERE id=%s", (document_id,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"Unknown document id: {document_id}")
    raw = row["raw_text"]
    if article is not None:
        if start is not None or end is not None:
            raise ValueError("Choose article label or character interval, not both")
        found = article_by_label(raw, article)
        start, end = found.start, found.end
    a = 0 if start is None else start
    b = len(raw) if end is None else end
    if not (0 <= a <= b <= len(raw)):
        raise ValueError("Invalid character range")
    return row["path"], raw[a:b]



def list_documents(conn, *, offset: int = 0, limit: int = 50) -> dict:
    """List indexed metadata without reading source files or loading raw text."""
    if type(offset) is not int or offset < 0 or offset > 100000:
        raise ValueError("Invalid offset")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid limit")
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS total FROM rag_documents")
        total = cur.fetchone()["total"]
        cur.execute(
            """SELECT d.id, d.path, d.parser, d.embedding_model, d.indexed_at,
                      length(d.raw_text) AS text_chars,
                      jsonb_array_length(d.warnings) AS warnings_count,
                      count(c.id) AS chunks,
                      count(c.embedding) AS embeddings
               FROM rag_documents d
               LEFT JOIN rag_chunks c ON c.document_id = d.id
               GROUP BY d.id
               ORDER BY d.path, d.id
               LIMIT %s OFFSET %s""",
            (limit, offset),
        )
        docs = [{
            "document_id": row["id"],
            "path": row["path"],
            "parser": row["parser"],
            "indexed_at": row["indexed_at"].isoformat(),
            "text_chars": row["text_chars"],
            "chunks": row["chunks"],
            "embeddings": row["embeddings"],
            "embedding_model": row["embedding_model"],
            "warnings_count": row["warnings_count"],
            "has_warnings": row["warnings_count"] > 0,
            "potential_issue": (
                "empty_text" if row["text_chars"] == 0 else
                "no_chunks" if row["chunks"] == 0 else
                "missing_embeddings" if row["embedding_model"] != "none"
                and row["embeddings"] < row["chunks"] else None
            ),
        } for row in cur.fetchall()]
    next_offset = offset + len(docs)
    return {
        "total_documents": total, "offset": offset, "limit": limit,
        "documents": docs,
        "next_offset": next_offset if next_offset < total else None,
        "source_check": "not_performed",
        "note": "Metadata din indexul PostgreSQL, nu din fisierele sursa. "
                "Pentru integritatea fragmentelor foloseste document_status.",
    }


def document_status(conn, document_id: int) -> dict:
    """Check exact extracted text coverage and stored embeddings for one document.

    This does not compare the stored hash to a live file: the isolated API
    intentionally does not mount the source documents.
    """
    if type(document_id) is not int or document_id <= 0:
        raise ValueError("Invalid document ID")
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, path, sha256, parser, raw_text, warnings,
                      embedding_model, indexed_at
               FROM rag_documents WHERE id=%s""",
            (document_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Unknown document id: {document_id}")
        cur.execute(
            """SELECT ordinal, char_start, char_end, content,
                      embedding IS NOT NULL AS has_embedding
               FROM rag_chunks WHERE document_id=%s ORDER BY ordinal""",
            (document_id,),
        )
        chunks = cur.fetchall()
    from .chunking import Chunk, verify_coverage

    raw_text = row["raw_text"]
    errors = []
    expected_ordinals = list(range(len(chunks)))
    if [item["ordinal"] for item in chunks] != expected_ordinals:
        errors.append("Non-contiguous chunk ordinals")
    if any(item["content"] != raw_text[item["char_start"]:item["char_end"]]
           for item in chunks):
        errors.append("Chunk content differs from indexed raw text")
    try:
        verify_coverage(
            raw_text, [
                Chunk(item["char_start"], item["char_end"], item["content"], [])
                for item in chunks
            ],
        )
    except ValueError as exc:
        errors.append("Invalid text coverage: " + str(exc)[:250])
    embeddings = sum(bool(item["has_embedding"]) for item in chunks)
    if row["embedding_model"] != "none" and embeddings != len(chunks):
        errors.append("Missing embeddings for indexed model")
    if row["embedding_model"] == "none" and embeddings:
        errors.append("Unexpected embeddings for lexical-only index")

    if not raw_text:
        status = "empty" if not errors else "incomplete"
    else:
        status = "indexed_complete" if not errors else "incomplete"
    return {
        "document_id": row["id"],
        "path": row["path"],
        "source_sha256": row["sha256"],
        "parser": row["parser"],
        "indexed_at": row["indexed_at"].isoformat(),
        "text_chars": len(raw_text),
        "chunks": len(chunks),
        "embeddings": embeddings,
        "embedding_model": row["embedding_model"],
        "warnings": row["warnings"],
        "warnings_count": len(row["warnings"]),
        "integrity_errors": errors,
        "stored_text_coverage_complete": not errors,
        "status": status,
        "source_check": "not_performed",
        "note": "Verificare doar a textului si fragmentelor stocate. "
                "Nu confirma actualitatea fisierului original.",
    }


def verify_index(conn) -> dict:
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS count FROM rag_chunks c
            JOIN rag_documents d ON d.id=c.document_id
            WHERE c.content IS DISTINCT FROM substring(
                d.raw_text FROM c.char_start + 1 FOR c.char_end - c.char_start
            ) OR c.char_end > length(d.raw_text)"""
        )
        mismatches = cur.fetchone()["count"]
        cur.execute(
            """SELECT d.id,d.path,d.raw_text,c.char_start,c.char_end,c.content
            FROM rag_documents d
            LEFT JOIN rag_chunks c ON c.document_id=d.id
            ORDER BY d.id,c.ordinal"""
        )
        current_doc = None
        raw_text = ""
        ranges = []
        invalid = []
        from .chunking import Chunk
        for row in cur.fetchall():
            if current_doc is not None and row["id"] != current_doc:
                try:
                    verify_coverage(raw_text, ranges)
                except ValueError as error:
                    invalid.append(str(error))
                ranges = []
            current_doc = row["id"]
            raw_text = row["raw_text"]
            if row["char_start"] is not None:
                ranges.append(Chunk(row["char_start"], row["char_end"], row["content"], []))
        if current_doc is not None:
            try:
                verify_coverage(raw_text, ranges)
            except ValueError as error:
                invalid.append(str(error))
        return {"content_mismatches": mismatches, "coverage_errors": invalid}
