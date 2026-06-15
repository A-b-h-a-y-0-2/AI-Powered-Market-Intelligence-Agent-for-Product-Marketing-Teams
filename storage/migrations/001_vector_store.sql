-- Market Intelligence Agent — Supabase vector store schema
-- Run this once in the Supabase SQL Editor (Dashboard → SQL Editor → New query → Run)
-- Safe to re-run: uses IF NOT EXISTS / OR REPLACE

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Recreate event_embeddings with the correct schema
--    (DROP + CREATE because existing table has wrong columns)
DROP TABLE IF EXISTS event_embeddings;

CREATE TABLE event_embeddings (
    id            bigserial PRIMARY KEY,
    event_id      text UNIQUE NOT NULL,          -- MongoDB _id of the parent event
    company       text NOT NULL,
    event_type    text NOT NULL,
    "timestamp"   text NOT NULL,                 -- ISO 8601 string (quoted: reserved word)
    summary       text NOT NULL,
    embedding     vector(384),                  -- bge-small-en-v1.5 dimensions
    stakeholder_tags text[] DEFAULT '{}',
    created_at    timestamptz DEFAULT now()
);

-- 3. Indexes
CREATE INDEX ON event_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
CREATE INDEX ON event_embeddings (company);
CREATE INDEX ON event_embeddings ("timestamp");
CREATE INDEX ON event_embeddings USING gin (stakeholder_tags);

-- 4. Semantic search function (called by VectorStore.semantic_search)
CREATE OR REPLACE FUNCTION match_event_embeddings(
    query_embedding   vector(384),
    match_count       int            DEFAULT 10,
    min_similarity    float          DEFAULT 0.0,
    filter_company    text           DEFAULT NULL,
    filter_event_types text[]        DEFAULT NULL,
    filter_since      text           DEFAULT NULL,
    filter_stakeholder_tag text      DEFAULT NULL
)
RETURNS TABLE (
    event_id          text,
    company           text,
    event_type        text,
    "timestamp"       text,
    summary           text,
    stakeholder_tags  text[],
    similarity        float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        e.event_id,
        e.company,
        e.event_type,
        e."timestamp",
        e.summary,
        e.stakeholder_tags,
        1 - (e.embedding <=> query_embedding) AS similarity
    FROM event_embeddings e
    WHERE
        (filter_company IS NULL OR e.company = filter_company)
        AND (filter_event_types IS NULL OR e.event_type = ANY(filter_event_types))
        AND (filter_since IS NULL OR e."timestamp" >= filter_since)
        AND (filter_stakeholder_tag IS NULL OR filter_stakeholder_tag = ANY(e.stakeholder_tags))
        AND 1 - (e.embedding <=> query_embedding) >= min_similarity
    ORDER BY e.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
