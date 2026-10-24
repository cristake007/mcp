"""Optional RAG read-only HTTP API, never exposed on a host port.

Only activated using rag/compose.mcp.yaml. It does not import MCP code,
modify the MCP database, or run automatically with the normal RAG commands.
"""
import asyncio
import hmac
import json
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def auth_token() -> str:
    secret = os.environ.get("RAG_API_TOKEN", "")
    if len(secret) < 32 or secret.startswith("CHANGE_ME"):
        raise RuntimeError("RAG_API_TOKEN must be at least 32 unpredictable characters")
    return secret


async def process(request: Request):
    expected = auth_token()
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied, "Bearer " + expected):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # Starlette's body() has no size limit: enforce one while streaming.
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 4096:
            return JSONResponse({"error": "request too large"}, status_code=413)
    try:
        params = json.loads(body)
        if not isinstance(params, dict):
            raise ValueError("Expected JSON object")
        from . import store
        from .embeddings import enabled

        if request.url.path == "/stats":
            from .stats import corpus_stats
            term = params.get("term")
            second = params.get("second_term")
            mode = params.get("mode", "all")
            examples_limit = params.get("examples_limit", 10)
            # Validate before opening a DB connection, including types.
            from .stats import validate
            validate(term, second, mode, examples_limit)

            def calculate():
                with store.connect() as stats_conn:
                    return corpus_stats(
                        stats_conn, term, second,
                        mode=mode, examples_limit=examples_limit,
                    )

            # Full-corpus counting must not block the API event loop.
            value = await asyncio.to_thread(calculate)
            return JSONResponse(value, headers={"Cache-Control": "no-store"})

        with store.connect() as conn:
            if request.url.path == "/search":
                query = params.get("query")
                limit = params.get("limit", 6)
                if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 12:
                    raise ValueError("Invalid query or limit")
                value = store.search(conn, query, limit=limit, use_vectors=enabled())
                return JSONResponse({"results": value}, headers={"Cache-Control": "no-store"})

            if request.url.path == "/text":
                document_id = params.get("document_id")
                start = params.get("start")
                end = params.get("end")
                article = params.get("article")
                if type(document_id) is not int or document_id <= 0:
                    raise ValueError("Invalid document ID")
                if start is not None and (type(start) is not int or start < 0):
                    raise ValueError("Invalid start")
                if end is not None and (type(end) is not int or end < 0):
                    raise ValueError("Invalid end")
                if article is not None and (not isinstance(article, str) or len(article) > 25):
                    raise ValueError("Invalid article")
                path, content = store.get_text(
                    conn, document_id, start, end, article=article
                )
                if len(content) > 25_000:
                    raise ValueError(
                        "Passage exceeds 25000 characters; use bounded start/end from search"
                    )
                return JSONResponse(
                    {"document_id": document_id, "path": path, "text": content,
                     "exact": True, "extraction_notice": "Literal extracted text; consult original for scan/layout issues"},
                    headers={"Cache-Control": "no-store"},
                )

        return JSONResponse({"error": "not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        # Never expose DB credentials or stack traces to MCP tool users.
        return JSONResponse({"error": "RAG unavailable"}, status_code=503)


app = Starlette(routes=[
    Route("/search", process, methods=["POST"]),
    Route("/text", process, methods=["POST"]),
    Route("/stats", process, methods=["POST"]),
])


def main():
    import uvicorn
    auth_token()
    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()
