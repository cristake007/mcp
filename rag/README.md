# Local RAG (optional, independent of MCP)

This directory is a **separate Docker Compose project** (`mcp-rag`).
Nothing in the root MCP `compose.yaml`, `app/`, `Dockerfile`, environment
or database is used or changed by RAG. There is no mandatory MCP connection and no web UI. A private API is optional. The RAG stack exposes **no host ports**. It has its own
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

Files are mounted read-only into the indexer and CLI containers and ignored by Git.

## Automatic indexing (recommended)

Start the standalone RAG service from the repository root:

```bash
cd /opt/mcp
./mcpctl rag start
./mcpctl rag logs
```

After that, just add PDF, DOCX, PPTX, XLSX, TXT or Markdown files to
`/opt/mcp/rag/documents/` (subfolders are supported). The independent Docker
`indexer` scans recursively every **60 seconds** and updates PostgreSQL +
embeddings automatically. MCP does not need to be running or connected.

New files must be unchanged across two scans, and have a modification time
at least **20 seconds old**. Most files appear within 1–2 minutes. Parser
errors are logged and retried. A partially copied or changed file does not
replace its last valid indexed version. For long-running copies, prefer
uploading to a temporary filename and renaming it into place when complete.

Deleted files are automatically removed from the index after two consistent
folder snapshots; as a safety guard an **empty** documents folder never
triggers a mass deletion. Set `RAG_AUTO_PRUNE=0` to disable automatic deletion.

Optional settings in `rag/.env` (Compose defaults work without adding them):

```dotenv
RAG_SCAN_INTERVAL_SECONDS=60
RAG_SETTLE_SECONDS=20
RAG_AUTO_PRUNE=1
```

Run `./mcpctl rag start` again after changing these settings. Use
`./mcpctl rag stop` to stop the indexer and its database (after disconnecting
the optional MCP bridge, if it is active).

### Manual diagnostics (optional)

The indexer initializes the schema itself. You do not need to run these
commands for normal operation, but they remain available for troubleshooting:

```bash
cd /opt/mcp/rag
docker compose run --rm cli verify
docker compose run --rm cli status
docker compose run --rm cli scan
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
  indexed automatically when the independent RAG indexer is running.

## Corpus statistics: Romanian word forms or exact terms

The optional MCP tool `corpus_stats` scans the **entire indexed corpus**,
counting occurrences, matching documents and articles. It does not rely
on the top results of vector search, nor does it use an LLM.

**Default: `match_type=linguistic`** (Romanian Snowball stemming): common
inflected word forms such as `suspendare` and `suspendarea` match as a word
family. Matching uses complete tokens, not prefixes or arbitrary substrings.
This is heuristic word stemming, **not full lemmatization or semantic
equivalence**; some related forms may still be missed or conflated.

**Optional: `match_type=exact`**: literal term or phrase matches as before,
ignoring capitalization and accents but respecting word boundaries.

```bash
cd /opt/mcp
./mcpctl rag stats "autorizare" --second-term "suspendare"
./mcpctl rag stats "autorizare" --second-term "suspendare" --match exact
```

`mode=all` (default) requires both terms within the **same document**
for `matching_documents`, or in the **same detected article** for
`matching_articles`. These counts may differ. `mode=any` matches either
term. `occurrences_per_term` is a count of individual matching word
forms, not a count of related concepts.

Article detection requires recognized headings at line start; other
documents still contribute to document counts. Totals are complete for
the extracted text, but listed examples are capped at 10 by default.
Sources include document IDs and character ranges usable with `get_text`.

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
printf 'RAG_API_TOKEN=%s\n' "$(openssl rand -hex 32)" >> .env
docker network create mcp-rag-link
docker compose -f compose.yaml -f compose.mcp.yaml up -d --build api

# Enable the tools only on the MCP container; --no-deps leaves its DB alone.
cd /opt/mcp
docker compose --env-file .env --env-file rag/.env \
  -f compose.yaml -f compose.rag.yaml up -d --no-deps --build mcp
```

If the Docker network already exists, skip `docker network create`. Use
the **same token** in both projects by keeping it in `rag/.env`, supplied
as an additional Compose env file to the MCP overlay.

Only with the overlays active does MCP advertise `search_docs`,
`get_text`, `corpus_stats`, `list_docs`, `document_status`, `corpus_health` and `get_page_image`
(unless individually disabled). The RAG API is reachable only over the opt-in
`mcp-rag-link` Docker network and requires a shared bearer secret.
The HTTP API offers only read-only POST `/search`, `/text`, `/stats`,
`/documents`, `/document-status`, `/corpus-health`, `/page-image`; no document
writing, admin endpoints, browser UI or public listening ports.

`search_docs` returns literal indexed chunks, their source offsets,
location metadata, RRF score and (when a legal article heading is
recognizable) its exact extracted text or its full character range.
The article assignment is heuristic for chunks crossing headings; verify
legal quotes against the original document. `get_text` returns literal
stored text by document ID with optional `start`/`end` or `article`
(e.g. `"Art. 34"`). Ambiguous/repeated article numbers require offsets.
HTTP text output is limited to 25,000 characters per call.

### Automatic PDF orientation correction (no file changes)

You can drop the **original rotated PDF** directly into \`rag/documents/\`.
The RAG indexer automatically determines a correction for each page before
extracting native text and running Tesseract OCR. It first examines the
dominant native text direction from PDF glyph matrices (handles 90°, 180°,
and 270° rotations even when the PDF metadata incorrectly says 0°).
Pages with little/no native text optionally use Tesseract OSD, but a low-
confidence OSD guess is ignored rather than rotating blindly.

The corrected PDF exists **only as a temporary file** during indexing.
Neither original bytes nor the user's source PDF are modified. PostgreSQL
records the applied page rotations under \`rag_documents.page_rotations\`,
and \`get_page_image\` uses the same metadata to display an upright page
from the unchanged original source. Page numbering stays unchanged.
\`document_status\` reports exact page angles and \`list_docs\` reports the
number of rotated pages. Orientation-related schema changes are additive
and retain the existing document IDs and previous index.

Optional setting in \`rag/.env\`:

\`\`\`dotenv
RAG_AUTO_ROTATE_ENABLED=1
\`\`\`

Existing PDFs get **one automatic reindex** after deployment because the
orientation algorithm version is new. Subsequent scans skip unchanged
documents. For mixed-layout PDFs or pages containing only a few characters
the orientation may remain unresolved; the engine does not guess a
rotation without sufficient evidence. No file renaming, manual conversion,
extra service, or new permanent file is required.

### Figure-aware OCR with local Tesseract (general PDF indexing)

The independent indexer now extracts **image-region text** from illustrated
PDF documents with Tesseract 5 (CPU-only, 1 OCR thread). It works on any PDF,
not on a predefined MANTIS/CAPTURE document template:

1. `pdfplumber` extracts the original text exactly as before, retaining its
   character offsets and page citations.
2. The OCR worker detects embedded image regions, associates the closest
   nearby native "Figure 4: ..." / "Figura 4: ..." caption by page geometry,
   and reads labels inside those regions with local Tesseract TSV OCR.
   A page without native text is OCR-scanned as a page-level fallback.
3. PostgreSQL stores `rag_figures` (page, region, caption) and
   `rag_ocr_chunks` (recognized labels, OCR confidence, geometry, figure ID,
   optional multilingual vector). No image binaries are stored in the DB.
4. `search_docs` searches text chunks **and** figure OCR results. OCR hits
   have `source_type="ocr_figure"`, `ocr_text`, `ocr_confidence`,
   `figure.caption`, `figure.page` and `locations[].page`. They intentionally
   have **no** `char_start`/`char_end`, since OCR is not the literal PDF
   text. Call `get_page_image(document_id, page)` to include the original
   visual page alongside the factual answer.
5. OCR and native text remain distinct, preserving `verify_index` and
   legal literal quoting via `get_text`. Incorrect OCR is never treated as
   guaranteed verbatim source text.

The OCR engine is included in the existing RAG Docker image
(`tesseract-ocr`, English/Romanian packs); no additional container, GPU,
cloud model, web API, or per-document hard-coded layout rules are needed.
The indexer uses `OMP_THREAD_LIMIT=1` when running OCR; images are rendered
and discarded during indexing. Figure association is *best effort* based
on geometry and captions, not perfect document understanding. Vector-only
illustrations, text inside tiny icons, heavily stylized fonts, or very dense
pages can still be missed or misread. Scanned PDFs without native text are
processed using a whole-page fallback. Only the first 12 larger image regions
per page are OCR-processed; exceeding this produces an index warning.

Optional env vars in `rag/.env` (the defaults work):

```dotenv
RAG_OCR_ENABLED=1
RAG_OCR_LANG=eng
```

For Romanian pages, change the language to `eng+ron`. Existing PDFs are
automatically reindexed once because the indexed `ocr_version` is upgraded,
even when source SHA-256 is unchanged. Subsequent scans of unchanged files
skip OCR. To check ingestion, use `list_docs` (new `figures` and
`ocr_chunks` counts), `document_status`, and `corpus_health`.
The first OCR pass over a large illustrated manual can use CPU for several
minutes; the indexer remains single-process and non-destructive. The API
never runs OCR per user question.

### Interactive RAG viewer in ChatGPT (MCP Apps)

The optional MCP stack now exposes **`show_rag_sources`**, a renderer tool
that opens a small, read-only HTML interface inside MCP Apps-capable clients
such as ChatGPT. The original search and image tools remain independent;
text-only clients receive structured/text results normally.

- `show_rag_sources(query="Ce conectori are MANTIS?")`: searches the
  **local RAG only**, selects up to four grounded PDF pages, and opens the
  page viewer automatically when the MCP host supports Apps.
- `show_rag_sources(document_id=N, page=P)`: opens a specific indexed PDF
  page, using IDs returned by `search_docs` (never invented).
- The viewer shows document names, captions, OCR snippets, zoom, and previous/
  next page navigation. This interface is static, bundled with the MCP image,
  and does not load any script, stylesheet, font or image from the Internet.
- The app-only tool `get_rag_page_data` transfers JPEG bytes from the
  authenticated private `/page-image` RAG API. The API verifies the indexed
  PDF SHA-256 before rendering every page; no static public document URL,
  direct UI access to PostgreSQL, or PDF parsing by the model/client is used.
- The HTML resource is `ui://rag/document-viewer/v1.html` with MIME type
  `text/html;profile=mcp-app`, linked by `_meta.ui.resourceUri`. Other
  clients continue to use `search_docs` and `get_page_image`.

**Limitations:** ChatGPT must choose the `show_rag_sources` tool (or you
select the MCP connector explicitly). The server cannot guarantee model
tool selection, prevent web browsing elsewhere, or require the client to
render an iframe. The view displays **full original PDF pages**, not cropped
figures, and its suggested pages may include imperfect OCR matches. It
does not alter indexed document data or existing search rankings.

Refresh tools after restarting the MCP server to discover
`show_rag_sources`. If the MCP Apps view is not supported or a UI call
fails, request the normal `get_page_image` tool or inspect source metadata.
Disable both viewer tools if you don't want this UI:
`./mcpctl tools disable show_rag_sources` and
`./mcpctl tools disable get_rag_page_data`.

### Images from indexed PDF manuals: `get_page_image`

PDF search results from `search_docs` already include source `locations`
(for example `{"kind":"pdf_page","page":13}`). To return the *original
illustration* while answering a question, use `get_page_image` with the
`document_id` from the search result and a **real PDF page number** from its
location metadata. The tool returns two native MCP content blocks: a text
description with source path/page and an `image/jpeg` image of the
**complete original PDF page**. It does not generate or invent an illustration.

`get_page_image(document_id=12, page=13)`

The PDF is loaded on demand from `rag/documents`, through the already
existing **read-only mount in the private RAG API**, never through MCP's file
system. The endpoint accepts only indexed document IDs; it rejects traversal,
symlinks, missing files, unsupported formats, invalid page numbers and PDF
files whose current SHA-256 does not match the index. It rechecks the file
after rendering, to detect concurrent changes. No images or base64 are
stored in PostgreSQL, and no new vector model, OCR, cloud provider or public
image URL is used.

Rendered JPEG output is limited to at most 1050 x 1500 pixels and 850 KiB,
so it can travel through the private MCP bridge. Source page images are
visually faithful *renderings*, not lossless PNGs of the original images.
The page-image tool itself does no OCR. The **indexer** now extracts OCR
labels from embedded image regions and makes them searchable by `search_docs`.
This does not equal full visual understanding of photographs or diagrams. If a page includes multiple
figures, the tool returns the full page rather than choosing a figure.

Native MCP image blocks are supported by the protocol and FastMCP, but their
visibility inside individual clients (including ChatGPT) depends on the
client UI. If the client does not display the image inline, this is a client
display limitation, not a sign that the server returned an invented image.
No public file link is exposed as a fallback.

To activate after `git pull`, run `./mcpctl rag enable` from the repository
root, then refresh the MCP tools in ChatGPT. Place the manual under
`rag/documents/`, let the indexer ingest it, and check `corpus_health`.
The original PDF is never modified.

### Document inventory and index health (stage 3)

The optional MCP tool `list_docs(offset=0, limit=50)` lists indexed
documents, including the original relative path, parser, last indexed time,
stored text size, number of chunks, embedding count and parser warning count.
Results are paginated via `next_offset`; `total_documents` counts indexed
records in PostgreSQL. It does not expose full document contents.

`document_status(document_id)` verifies one document's **stored index**:
every extracted character is covered by chunks, stored chunk content agrees
with extracted raw text, chunk ordinals are contiguous, and embeddings match
the recorded model. It also lists all extraction warnings. Results indicate
`indexed_complete`, `incomplete`, or `empty`; extraction warnings are
reported separately and need examination.

The `list_docs` and `document_status` tools inspect only PostgreSQL.
They do not compare original files. The independent `corpus_health` tool
performs that comparison separately using a **read-only** document mount
available only inside the optional RAG API container, never MCP itself.
`source_check="not_performed"` for `document_status` remains accurate.
Automatic scanning is performed separately by the independent indexer.

### Source directory versus PostgreSQL: `corpus_health`

`corpus_health(offset=0, limit=50)` compares every supported original file
recursively in `rag/documents` with the PostgreSQL `rag_documents` table.
It reads filesystem bytes to calculate SHA-256 and compares with the hash
saved at indexing time. It performs **no indexing, deletion or write**.

The result includes **global exhaustive counts** in `summary` and
paginated file details in `files` (follow `next_offset`):

- `current`: exact content hash matches indexed source hash.
- `new_unindexed`: supported file exists, but there is no index row.
- `modified`: file exists, but differs from the indexed source hash.
- `missing`: PostgreSQL entry exists, but the source file is absent.
- `unsupported`, `oversized`, `unreadable`, `ignored_symlink`:
  files requiring attention, not silently counted as successfully indexed.

`summary.new_files` and `summary.unindexed_files` both count the **same**
`new_unindexed` documents; do not add them together. File additions with
unsupported extensions are shown separately. The `status` is `healthy`
only when no differences/errors were detected. An unexpectedly empty source
directory with a nonempty index produces
`source_directory_empty_check_mount`, **not** a healthy or definitely
deleted corpus. A missing source-directory mount fails explicitly.

This is a **best-effort live comparison**, not an atomic snapshot between the
filesystem and PostgreSQL: a concurrent indexer can change state during the
scan. Re-run after indexing settles for a stable result. The scan reports
source freshness and inventory coverage; `document_status` separately
checks extracted-text/chunk fidelity and parser warnings. The RAG API has
read-only access to `./documents:/documents:ro`; the MCP container never
receives this mount. The original documents stay untouched.

Example MCP call:

```json
{"offset":0,"limit":50}
```

Use the returned `summary` to see whether any documents are unindexed or
outdated; follow `next_offset` to list every affected path.

After updating RAG source code, run `./mcpctl rag enable` from the repository
root if the bridge is active. It rebuilds the private RAG API **and** the MCP
container without deleting documents or their index. If RAG is disabled and
only site-audit improvements are needed, use `./mcpctl mcp apply`.

There is no automatic shared-secret provisioner. Documents are indexed
by the separate RAG indexer when running. The internal API loads the local
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
