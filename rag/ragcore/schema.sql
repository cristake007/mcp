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
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
