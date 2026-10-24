-- Private to the standalone mcp-rag PostgreSQL database.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
    id BIGSERIAL PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    sha256 CHAR(64) NOT NULL,
    parser TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding_model TEXT NOT NULL DEFAULT 'none',
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ocr_version INTEGER NOT NULL DEFAULT 0
);
-- Additive migration for already deployed PostgreSQL volumes.
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS ocr_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS rag_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL CHECK (char_end > char_start),
    content TEXT NOT NULL,
    locations JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding vector(384),
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('romanian', content)) STORED,
    UNIQUE (document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS rag_chunks_tsv_idx ON rag_chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS rag_chunks_document_idx ON rag_chunks (document_id);
-- No approximate vector index is needed for ~100 documents; exact cosine distance is used.


-- OCR and figures are separate from literal pdfplumber raw_text and rag_chunks.
-- No copied image bytes are persisted; coordinates reference the original PDF.
CREATE TABLE IF NOT EXISTS rag_figures (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL CHECK (page > 0),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    kind TEXT NOT NULL,
    bbox JSONB NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    UNIQUE (document_id, page, ordinal)
);
CREATE INDEX IF NOT EXISTS rag_figures_doc_page_idx
    ON rag_figures(document_id, page);

CREATE TABLE IF NOT EXISTS rag_ocr_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    figure_id BIGINT NOT NULL REFERENCES rag_figures(id) ON DELETE CASCADE,
    page INTEGER NOT NULL CHECK (page > 0),
    content TEXT NOT NULL,
    ocr_text TEXT NOT NULL,
    bbox JSONB NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    embedding vector(384),
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('romanian', content)) STORED
);
CREATE INDEX IF NOT EXISTS rag_ocr_chunks_tsv_idx ON rag_ocr_chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS rag_ocr_chunks_doc_idx ON rag_ocr_chunks(document_id);
