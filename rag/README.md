# Local RAG (optional, independent of MCP)

This directory is a **separate Docker Compose project** (`mcp-rag`).
Nothing in the root MCP `compose.yaml`, `app/`, `Dockerfile`, environment
or database is used or changed by RAG. There is **no MCP tool or API** here
and no web UI. The RAG stack exposes **no host ports**. It has its own
PostgreSQL 18 + pgvector database, private Docker network and named volumes.

## Prepare, from the server

```bash
cd /opt/mcp/rag
mkdir -p documents
cp .env.example .env
# Replace CHANGE_ME_TO_A_LONG_RANDOM_PASSWORD in rag/.env with:
openssl rand -hex 32
chmod 600 .env
```

Paste the generated password into `rag/.env` (not root `.env`).
Store documents beneath `rag/documents/`, with any directory depth:

```text
rag/documents/
├── certificari/
│   ├── iso9001.pdf
│   ├── standard.docx
│   └── instruire.pptx
└── proceduri/
    ├── reguli.xlsx
    └── subdirector/
        └── ghid.md
```

Files are mounted read-only into the CLI container and are ignored by Git.

## First run

```bash
docker compose up -d database
docker compose run --rm cli init
docker compose run --rm cli scan
docker compose run --rm cli verify
docker compose run --rm cli status
```

The first semantic scan downloads `intfloat/multilingual-e5-small` ONNX
(weights are cached in a separate RAG Docker volume). FastEmbed uses CPU.
There is no LLM download or LLM server. To **avoid model downloads/RAM usage**
while testing the parser, use `scan --lexical-only` and
`search --lexical-only`. A later normal `scan` creates the vectors.

```bash
docker compose run --rm cli scan --lexical-only
docker compose run --rm cli search --lexical-only "Articolul 15"
docker compose run --rm cli search "Ce prevede ISO 9001?"
docker compose run --rm cli get-text 1 --start 0 --end 300
docker compose run --rm cli scan --prune
```

`search` returns JSON with document ID, relative path, exact chunk text,
zero-based character range `[char_start,char_end)`, location metadata and
RRF score. `get-text` writes *only* the literal extracted text for one
document and optional range. No MCP client is connected to these commands.

`scan` is manual and recursive. SHA-256 unchanged files are skipped; changed
files are atomically replaced. `--prune` removes indexed entries for deleted
files, but refuses to prune if the document directory is empty or any file
failed to parse. Files with unsupported extensions are ignored. Files over
30 MiB fail visibly without destroying the previous index. Parsing errors
produce a nonzero CLI exit code.

## Fidelity guarantees and limitations

- Store the **complete extracted text** (UTF-8) per document, not only chunks.
  Every stored chunk corresponds exactly to a zero-based interval in that
  extracted text. `verify` checks content equality and full character
  coverage for every document.
- Each extracted PDF page, DOCX paragraph/table cell, PPTX slide/text shape/
  table cell/presenter note, and XLSX cell stores provenance; sources are
  returned with search results.
- Extraction is **not** a byte-for-byte reconstruction of the original:
  PDFs may have reading-order issues; image-only PDFs need OCR and are
  explicitly flagged. Embedded shapes, footnotes and tracked DOCX changes
  may require manual inspection. PPTX slide text, tables and presenter notes
  are supported; reading order follows shape/XML order and may differ from
  visual reading order. Images, charts, SmartArt and embedded objects are
  not fully parsed and are flagged where detected. XLSX formulas are stored
  as formulas, **not evaluated values**. Original files remain untouched.
- Embeddings are for retrieval only. Exact quotes always come from stored
  extracted text, not from an LLM. Full-text uses PostgreSQL Romanian
  stemming, phrase substring matching and vector cosine similarity merged
  with RRF. Model uses `passage:` and `query:` prefixes.
- No approximate pgvector index for this small corpus; exact cosine search
  avoids approximate-index recall trade-offs. New or modified documents are
  processed only on explicit scan, never in a background process.

## Optional MCP connection (disabled by default)

**Normal MCP and normal RAG are still independent.** The bridge is an
additional service and two Compose overlays. It never exposes a host
port, never mounts source documents into MCP and never gives MCP direct
PostgreSQL credentials for RAG.

After pulling the latest code, enable the bridge only if desired:

```bash
cd /opt/mcp/rag
# Existing rag/.env and DB password must be retained. Append the shared
# bridge token only once. Do not commit this file.
printf 'RAG_API_TOKEN=%s\\n' "$(openssl rand -hex 32)" >> .env
docker network create mcp-rag-link
docker compose -f compose.yaml -f compose.mcp.yaml up -d --build api

# Enable the tools only on the MCP container; --no-deps leaves its DB alone.
cd /opt/mcp
docker compose --env-file .env --env-file rag/.env \\
  -f compose.yaml -f compose.rag.yaml up -d --no-deps --build mcp
```

If the Docker network already exists, skip `docker network create`. Use
the **same token** in both projects by keeping it in `rag/.env`, supplied
as an additional Compose env file to the MCP overlay.

Only with the overlays active does MCP advertise `search_docs` and
`get_text`. The RAG API is reachable only over the opt-in
`mcp-rag-link` Docker network and requires a shared bearer secret.
The HTTP API offers only POST `/search` and POST `/text`; no document
writing, admin endpoints, browser UI or public listening ports.

`search_docs` returns literal indexed chunks, their source offsets,
location metadata, RRF score and (when a legal article heading is
recognizable) its exact extracted text or its full character range.
The article assignment is heuristic for chunks crossing headings; verify
legal quotes against the original document. `get_text` returns literal
stored text by document ID with optional `start`/`end` or `article`
(e.g. `"Art. 34"`). Ambiguous/repeated article numbers require offsets.
HTTP text output is limited to 25,000 characters per call.

There is no automatic shared-secret or model provisioner. Index documents
from this RAG project's CLI as before. The internal API loads the local
embeddings model into RAM when semantic queries first arrive, and keeps it
for repeated searches. If the API is stopped, MCP's other tools still work.

### Disable again (keep independent RAG database/documents)

```bash
cd /opt/mcp
docker compose up -d --no-deps mcp
cd /opt/mcp/rag
docker compose -f compose.yaml -f compose.mcp.yaml stop api
```

The first command returns MCP to its normal configuration (without RAG
tools); the second stops the optional RAG API. Ordinary RAG CLI commands
and `rag/database` still work. No data migration is needed either way.
For complete RAG shutdown, use the normal RAG-only `docker compose down`
described below, from `/opt/mcp/rag` without the overlays.

## Completely optional / uninstall independently

```bash
cd /opt/mcp/rag
docker compose down
```

This stops **only RAG**. It has no effect on the separate root `custom-mcp`
stack. For irreversible deletion of *RAG-only* DB and model cache volumes:
`docker compose down -v`. This leaves host `rag/documents/` intact.
Never run `down -v` in the root MCP directory when managing RAG.

## Testing

```bash
python -m unittest discover -s rag/tests -v
```

Database integration tests run in the separate CI job with
`RAG_TEST_DB=1` and an isolated pgvector PostgreSQL service.

No RAG server runs by default after cloning or deploying the MCP stack.
