-- PostgreSQL table for Agent Memory persistent storage.
-- Run this script in the database configured by MEMORY_DATABASE_URL.

CREATE TABLE IF NOT EXISTS agent_memories (
    memory_key TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    source_thread_id TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_memories_source_thread_id
ON agent_memories (source_thread_id);

CREATE INDEX IF NOT EXISTS idx_agent_memories_updated_at
ON agent_memories (updated_at DESC);
