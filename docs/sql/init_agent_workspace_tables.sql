-- ChainCloud-AI Agent Workspace / Thread / Run / Tool Trace tables
-- Purpose:
--   Introduce a unified resource model for future Agent architecture evolution:
--
--   User
--     -> Workspace
--         -> Thread
--             -> Checkpoints
--             -> Agent Runs
--                 -> Tool Traces
--             -> Memories
--
-- Notes:
--   1. This file is designed to be additive and backward-compatible.
--   2. It does not drop or rewrite existing tables.
--   3. It intentionally uses TEXT identifiers to stay compatible with the current
--      lightweight auth / thread_id / memory_key implementation.
--   4. Foreign keys are not enforced in the first MVP to avoid breaking existing
--      local databases with slightly different historical schemas.

CREATE TABLE IF NOT EXISTS agent_workspaces (
    workspace_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    default_profile TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_workspaces_user_id
    ON agent_workspaces(user_id);

CREATE INDEX IF NOT EXISTS idx_agent_workspaces_archived_at
    ON agent_workspaces(archived_at);


CREATE TABLE IF NOT EXISTS agent_threads (
    thread_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT,
    profile_id TEXT,
    last_message_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_threads_workspace_id
    ON agent_threads(workspace_id);

CREATE INDEX IF NOT EXISTS idx_agent_threads_user_id
    ON agent_threads(user_id);

CREATE INDEX IF NOT EXISTS idx_agent_threads_last_message_at
    ON agent_threads(last_message_at);

CREATE INDEX IF NOT EXISTS idx_agent_threads_archived_at
    ON agent_threads(archived_at);


CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    profile_id TEXT,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'started',
    input_message TEXT,
    output_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    latency_ms INTEGER,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_thread_id
    ON agent_runs(thread_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_workspace_id
    ON agent_runs(workspace_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_user_id
    ON agent_runs(user_id);

CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at
    ON agent_runs(started_at);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status
    ON agent_runs(status);


CREATE TABLE IF NOT EXISTS agent_tool_traces (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input JSONB,
    tool_output JSONB,
    status TEXT NOT NULL DEFAULT 'started',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    latency_ms INTEGER,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_agent_tool_traces_run_id
    ON agent_tool_traces(run_id);

CREATE INDEX IF NOT EXISTS idx_agent_tool_traces_thread_id
    ON agent_tool_traces(thread_id);

CREATE INDEX IF NOT EXISTS idx_agent_tool_traces_workspace_id
    ON agent_tool_traces(workspace_id);

CREATE INDEX IF NOT EXISTS idx_agent_tool_traces_user_id
    ON agent_tool_traces(user_id);

CREATE INDEX IF NOT EXISTS idx_agent_tool_traces_tool_name
    ON agent_tool_traces(tool_name);

CREATE INDEX IF NOT EXISTS idx_agent_tool_traces_started_at
    ON agent_tool_traces(started_at);

CREATE INDEX IF NOT EXISTS idx_agent_tool_traces_status
    ON agent_tool_traces(status);


-- Backward-compatible enhancement for existing long-term memory table.
-- The existing agent_memories table is kept as-is, but gains optional fields
-- so memory can gradually become user-aware and workspace-aware.

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS user_id TEXT;

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS workspace_id TEXT;

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS memory_type TEXT NOT NULL DEFAULT 'summary';

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS tags TEXT[];

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS source_run_id TEXT;

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS importance INTEGER;

ALTER TABLE agent_memories
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_agent_memories_user_id
    ON agent_memories(user_id);

CREATE INDEX IF NOT EXISTS idx_agent_memories_workspace_id
    ON agent_memories(workspace_id);

CREATE INDEX IF NOT EXISTS idx_agent_memories_memory_type
    ON agent_memories(memory_type);

CREATE INDEX IF NOT EXISTS idx_agent_memories_source_thread_id
    ON agent_memories(source_thread_id);

CREATE INDEX IF NOT EXISTS idx_agent_memories_source_run_id
    ON agent_memories(source_run_id);

CREATE INDEX IF NOT EXISTS idx_agent_memories_last_used_at
    ON agent_memories(last_used_at);