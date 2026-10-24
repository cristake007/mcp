"""Exhaustive literal corpus statistics (not vector search or legal interpretation).

Counts use the complete stored extracted text of EVERY indexed document.
Search is case-insensitive, accent-insensitive and requires word boundaries
for terms starting/ending in a word character. Quotes remain literal phrases.
Recognized 'Art.' headings define articles; documents without headings still
count toward document/occurrence totals, not article totals.
"""
import re
import unicodedata

from .articles import articles


def normalize(value: str) -> str:
    """Romanian-friendly literal matching; never used to rewrite source data."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def phrase_pattern(term: str) -> re.Pattern:
    normalized = normalize(term.strip())
    if not normalized:
        raise ValueError("Term must not be empty")
    prefix = r"(?<!\w)" if normalized[0].isalnum() else ""
    suffix = r"(?!\w)" if normalized[-1].isalnum() else ""
    return re.compile(prefix + re.escape(normalized) + suffix)


def validate(term: str, second_term: str | None, mode: str,
             examples_limit: int) -> tuple[list[str], list[re.Pattern]]:
    if not isinstance(term, str) or not 1 <= len(term.strip()) <= 120:
        raise ValueError("term must contain 1-120 characters")
    if second_term is not None and (
        not isinstance(second_term, str) or not 1 <= len(second_term.strip()) <= 120
    ):
        raise ValueError("second_term must contain 1-120 characters or be null")
    if mode not in ("all", "any"):
        raise ValueError("mode must be 'all' or 'any'")
    if type(examples_limit) is not int or not 0 <= examples_limit <= 20:
        raise ValueError("examples_limit must be an integer from 0 to 20")
    terms = [term.strip()]
    if second_term is not None:
        terms.append(second_term.strip())
    return terms, [phrase_pattern(word) for word in terms]


def match_counts(normalized_text: str, patterns: list[re.Pattern]) -> list[int]:
    return [sum(1 for _ in pattern.finditer(normalized_text)) for pattern in patterns]


def meets(counts: list[int], mode: str) -> bool:
    return all(counts) if mode == "all" else any(counts)


def corpus_stats(conn, term: str, second_term: str | None = None,
                 *, mode: str = "all", examples_limit: int = 10) -> dict:
    """Count full corpus and report bounded examples, never sampled totals."""
    terms, patterns = validate(term, second_term, mode, examples_limit)

    totals = {
        "documents_scanned": 0,
        "articles_detected": 0,
        "matching_documents": 0,
        "matching_articles": 0,
        "documents_with_matching_articles": 0,
        "occurrences_per_term": [0] * len(terms),
    }
    document_examples = []
    article_examples = []
    # One SELECT yields a consistent PostgreSQL statement-level snapshot, so
    # concurrent incremental scans cannot give mixed document revisions.
    with conn.cursor() as cur:
        cur.execute("SELECT id, path, raw_text FROM rag_documents ORDER BY id")
        for row in cur:
            raw = row["raw_text"]
            doc_id = row["id"]
            path = row["path"]
            totals["documents_scanned"] += 1
            counts = match_counts(normalize(raw), patterns)
            for n, count in enumerate(counts):
                totals["occurrences_per_term"][n] += count
            document_matches = meets(counts, mode)
            if document_matches:
                totals["matching_documents"] += 1

            document_articles = articles(raw)
            totals["articles_detected"] += len(document_articles)
            matched_in_doc = 0
            for item in document_articles:
                article_counts = match_counts(normalize(raw[item.start:item.end]), patterns)
                if meets(article_counts, mode):
                    matched_in_doc += 1
                    totals["matching_articles"] += 1
                    if len(article_examples) < examples_limit:
                        article_examples.append({
                            "document_id": doc_id,
                            "path": path,
                            "article": item.label,
                            "char_start": item.start,
                            "char_end": item.end,
                            "occurrences_per_term": article_counts,
                        })

            if matched_in_doc:
                totals["documents_with_matching_articles"] += 1
            if document_matches and len(document_examples) < examples_limit:
                document_examples.append({
                    "document_id": doc_id,
                    "path": path,
                    "occurrences_per_term": counts,
                    "matching_articles": matched_in_doc,
                })

    return {
        "terms": terms,
        "mode": mode,
        "match_method": "literal phrase, case-insensitive and accent-insensitive; whole-word boundaries",
        "not_semantic": True,
        "exhaustive_counts": True,
        **totals,
        "examples_limit": examples_limit,
        "document_examples": document_examples,
        "article_examples": article_examples,
        "document_examples_truncated": totals["matching_documents"] > len(document_examples),
        "article_examples_truncated": totals["matching_articles"] > len(article_examples),
        "note": (
            "Co-occurrence is measured independently within each document or "
            "each detected article. A document can match with terms in different "
            "articles and then have zero matching articles. Counts are for extracted "
            "text, not equivalent concepts, implied meaning, or non-text PDF images. "
            "Article headings are heuristic; source offsets refer to exact extracted text."
        ),
    }
