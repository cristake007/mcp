"""Compare the RAG document directory against its PostgreSQL index.

This scanner is read-only: it never triggers indexing, pruning, or changes to
the source directory. It runs inside the optional RAG API container, not MCP.
"""
from collections import Counter
from pathlib import Path
import os

from .parsing import SUPPORTED
from .store import file_digest, init_schema

MAX_INDEXABLE_BYTES = 30 * 1024 * 1024


def corpus_health(conn, folder: Path, *, offset: int = 0, limit: int = 50) -> dict:
    """Inventory on-disk files and classify differences from indexed file hashes.

    Source/index checks are best-effort rather than an atomic filesystem/DB
    snapshot; files that change during digesting are marked unreadable.
    """
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError("offset must be between 0 and 100000")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    # A missing/unmounted directory is a hard failure, never a healthy empty corpus.
    if folder.is_symlink() or not folder.exists() or not folder.is_dir():
        raise RuntimeError("RAG documents directory is missing, unmounted or invalid")
    root = folder.resolve(strict=True)
    if root == Path("/"):
        raise RuntimeError("Refusing to inventory filesystem root")

    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, path, sha256, indexed_at FROM rag_documents ORDER BY path"
        )
        indexed = {row["path"]: row for row in cur.fetchall()}

    records = {}
    seen = set()
    ignored_symlinks = []
    # Recursively list both supported and unsupported files; the latter should
    # not disappear silently from an assertion that the corpus is complete.
    def onerror(exc):
        raise exc

    for current, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        for name in list(dirs):
            if (Path(current) / name).is_symlink():
                dirs.remove(name)
                ignored_symlinks.append((Path(current) / name).relative_to(root).as_posix() + "/")
        dirs.sort()
        for name in sorted(files):
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                ignored_symlinks.append(rel)
                continue
            if not path.is_file():
                # A special file (FIFO/device) is not a document and must not be read.
                records[rel] = {"path": rel, "status": "unsupported",
                                "reason": "not_a_regular_file"}
                continue
            if path.suffix.lower() not in SUPPORTED:
                records[rel] = {"path": rel, "status": "unsupported",
                                "reason": "unsupported_extension"}
                continue
            seen.add(rel)
            previous = indexed.get(rel)
            record = {"path": rel}
            if previous is not None:
                record["document_id"] = previous["id"]
                record["indexed_at"] = previous["indexed_at"].isoformat()
            try:
                before = path.stat()
                if before.st_size > MAX_INDEXABLE_BYTES:
                    record["status"] = "oversized"
                    record["size_bytes"] = before.st_size
                    record["reason"] = "file_exceeds_30_MiB_indexer_limit"
                else:
                    checksum = file_digest(path)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                        after.st_size, after.st_mtime_ns, after.st_ino
                    ):
                        record["status"] = "unreadable"
                        record["reason"] = "changed_during_hash"
                    else:
                        record["size_bytes"] = after.st_size
                        record["status"] = (
                            "new_unindexed" if previous is None else
                            "current" if checksum == previous["sha256"] else "modified"
                        )
            except OSError:
                record["status"] = "unreadable"
                record["reason"] = "cannot_read_or_stat"
            records[rel] = record

    for rel, previous in indexed.items():
        if rel not in seen and rel not in records:
            records[rel] = {
                "path": rel, "status": "missing",
                "document_id": previous["id"],
                "indexed_at": previous["indexed_at"].isoformat(),
            }
    for rel in ignored_symlinks:
        records[rel] = {"path": rel, "status": "ignored_symlink",
                        "reason": "symlinks_not_followed"}

    counts = Counter(row["status"] for row in records.values())
    supported_count = sum(counts[k] for k in
                          ("current", "new_unindexed", "modified", "oversized", "unreadable"))
    root_empty = not any(row["status"] not in ("ignored_symlink",)
                         for row in records.values() if row["status"] != "missing")
    # An empty source mount with indexed documents might be an operator/mount
    # problem, not a mass file deletion; surface the uncertainty explicitly.
    ambiguous_empty = root_empty and bool(indexed)
    has_issue = any(counts[k] for k in
                    ("new_unindexed", "modified", "missing", "unsupported",
                     "oversized", "unreadable", "ignored_symlink"))
    status = "attention_required" if has_issue else "healthy"
    if ambiguous_empty:
        status = "source_directory_empty_check_mount"

    entries = [records[key] for key in sorted(records)]
    page = entries[offset:offset + limit]
    next_offset = offset + len(page)
    return {
        "status": status,
        "source_directory_checked": True,
        "source_directory_empty": root_empty,
        "source_mount_warning": (
            "The documents directory is empty while PostgreSQL has indexed "
            "documents; verify the bind mount before treating files as deleted."
            if ambiguous_empty else None
        ),
        "summary": {
            "indexed_documents": len(indexed),
            "supported_files_on_disk": supported_count,
            "current_files": counts["current"],
            "new_files": counts["new_unindexed"],
            "unindexed_files": counts["new_unindexed"],
            "modified_files": counts["modified"],
            "missing_files": counts["missing"],
            "unsupported_files": counts["unsupported"],
            "oversized_files": counts["oversized"],
            "unreadable_files": counts["unreadable"],
            "ignored_symlinks": counts["ignored_symlink"],
        },
        "total_entries": len(entries),
        "offset": offset,
        "limit": limit,
        "files": page,
        "next_offset": next_offset if next_offset < len(entries) else None,
        "counts_scope": "all_files_and_index_entries",
        "entries_scope": "current_page",
        "read_only": True,
        "note": (
            "new_files si unindexed_files reprezinta aceleasi fisiere noi "
            "neindexate (nu se aduna). Se folosesc extensiile acceptate de "
            "indexer. Un fisier suportat deja indexat, dar schimbat fata de "
            "hash-ul salvat, apare ca modified. Comparatia nu este un "
            "snapshot atomic; scanarile in curs pot modifica rezultatul. "
            "Integritatea fragmentelor indexate se verifica prin document_status."
        ),
    }
