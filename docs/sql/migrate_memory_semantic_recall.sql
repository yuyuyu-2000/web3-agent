-- Backward-compatible automatic memory recall migration.
-- Existing rows remain valid; nullable embeddings may be backfilled later.
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS user_id TEXT;
ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS memory_type TEXT;
ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS embedding vector;

CREATE INDEX IF NOT EXISTS idx_agent_memories_user_id
ON agent_memories (user_id);

-- Preserve ownership already recorded by Memory v1 metadata.
UPDATE agent_memories
SET user_id = metadata->>'user_id'
WHERE user_id IS NULL AND metadata ? 'user_id';
