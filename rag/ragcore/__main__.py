"""Command-line entrypoint for the optional, independent RAG stack."""
import argparse
import json
import os
import sys
from pathlib import Path


def parser():
    root = argparse.ArgumentParser(description="Independent self-hosted document RAG")
    sub = root.add_subparsers(dest="action", required=True)
    sub.add_parser("init", help="Create RAG SQL schema (own database only)")
    scan = sub.add_parser("scan", help="Index all supported files recursively")
    scan.add_argument("--lexical-only", action="store_true",
                      help="Skip download/inference of the embeddings model")
    scan.add_argument("--prune", action="store_true",
                      help="Delete stale index entries (refuses empty directory)")
    search = sub.add_parser("search", help="Hybrid RRF retrieval (prints JSON)")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--lexical-only", action="store_true")
    text = sub.add_parser("get-text", help="Return literal stored extracted text")
    text.add_argument("document_id", type=int)
    text.add_argument("--start", type=int)
    text.add_argument("--end", type=int)
    text.add_argument("--article", help="e.g. 34 or Art. 34 (full extracted article)")
    stats = sub.add_parser("stats", help="Exact counts across ALL indexed documents and articles")
    stats.add_argument("term", help="Literal word or phrase to count")
    stats.add_argument("--second-term", default=None, help="Second term for co-occurrence")
    stats.add_argument("--mode", choices=("all", "any"), default="all")
    stats.add_argument("--match", choices=("linguistic", "exact"), default="linguistic",
                       help="Romanian word forms (default) or literal only")
    stats.add_argument("--examples-limit", type=int, default=10)
    sub.add_parser("verify", help="Verify stored chunk content and coverage")
    sub.add_parser("status", help="Show document/chunk counts")
    return root


def main():
    args = parser().parse_args()
    from . import store
    from .embeddings import enabled

    try:
        with store.connect() as conn:
            if args.action == "init":
                store.init_schema(conn)
                print("RAG database initialized")
            elif args.action == "scan":
                counts = store.scan(
                    conn, Path(os.getenv("RAG_DOCUMENTS", "/documents")),
                    use_vectors=enabled() and not args.lexical_only,
                    prune=args.prune,
                )
                print(json.dumps(counts, ensure_ascii=False))
                if counts["errors"]:
                    return 1
            elif args.action == "search":
                result = store.search(
                    conn, args.query, limit=args.limit,
                    use_vectors=enabled() and not args.lexical_only,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            elif args.action == "stats":
                from .stats import corpus_stats
                store.init_schema(conn)
                result = corpus_stats(
                    conn, args.term, args.second_term,
                    mode=args.mode, examples_limit=args.examples_limit,
                    match_type=args.match,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.action == "get-text":
                _, content = store.get_text(
                    conn, args.document_id, args.start, args.end, article=args.article
                )
                # No metadata, quoting, whitespace changes or explanatory prose.
                sys.stdout.write(content)
            elif args.action == "verify":
                result = store.verify_index(conn)
                print(json.dumps(result, ensure_ascii=False))
                return int(bool(result["content_mismatches"] or result["coverage_errors"]))
            elif args.action == "status":
                store.init_schema(conn)
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT (SELECT count(*) FROM rag_documents) AS documents,
                                  (SELECT count(*) FROM rag_chunks) AS chunks,
                                  (SELECT count(*) FROM rag_chunks
                                   WHERE embedding IS NOT NULL) AS embeddings"""
                    )
                    print(json.dumps(cur.fetchone(), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, Exception) as error:
        # Errors are visible to the operator, not quietly treated as a valid empty index.
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
