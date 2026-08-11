# Thread Checkpoint Persistence Verification

This document records how to verify normal chat thread context persistence with PostgreSQL checkpoint storage.

## Purpose

ChainCloud-AI currently has two different persistence paths:

- `MEMORY_DATABASE_URL`: long-term Agent Memory, stored in the `agent_memories` table.
- `DATABASE_URL`: normal chat thread checkpoint persistence, stored in the `checkpoint_*` tables.

This verification focuses on `DATABASE_URL`.

## Environment

Local Docker PostgreSQL example:

```env
POSTGRES_HOST_PORT=15432
DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev

MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

`DATABASE_URL` is an advanced option. It can be disabled for first-time local setup, especially on Windows. Enable it when validating normal chat thread checkpoint persistence.

## 1. Start PostgreSQL

```bash
docker compose up -d postgres
docker compose ps
```

Verify tables:

```bash
docker compose exec -T postgres psql -U chaincloud -d chaincloud_memory_dev -c "\dt"
```

Expected tables include:

```text
agent_memories
checkpoint_blobs
checkpoint_migrations
checkpoint_writes
checkpoints
```

## 2. Start backend

```bash
uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001
```

Expected result:

```text
Application startup complete.
Uvicorn running on http://0.0.0.0:8001
```

## 3. First chat turn

Send the first request with a fixed `thread_id`:

```bash
curl -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "checkpoint_demo_thread_001",
    "message": "请记住：我正在测试 ChainCloud Agent 的普通对话上下文持久化功能，测试关键词是 checkpoint-persistence-demo。",
    "debug": false
  }'
```

Expected behavior: the agent replies normally.

## 4. Same-process context check

Send a second request with the same `thread_id`:

```bash
curl -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "checkpoint_demo_thread_001",
    "message": "我刚才让你记住的测试关键词是什么？",
    "debug": false
  }'
```

Expected behavior: the agent should mention:

```text
checkpoint-persistence-demo
```

If the agent mentions this keyword, the same-process thread context is working.

## 5. Database checkpoint write check

Check whether checkpoint records have been written to PostgreSQL:

```bash
docker compose exec -T postgres psql -U chaincloud -d chaincloud_memory_dev -c "SELECT COUNT(*) AS checkpoints_count FROM checkpoints;"
docker compose exec -T postgres psql -U chaincloud -d chaincloud_memory_dev -c "SELECT COUNT(*) AS checkpoint_writes_count FROM checkpoint_writes;"
docker compose exec -T postgres psql -U chaincloud -d chaincloud_memory_dev -c "SELECT COUNT(*) AS checkpoint_blobs_count FROM checkpoint_blobs;"
```

Expected behavior: checkpoint tables should contain records.

## 6. Restart backend and verify persistence recovery

Stop the backend with:

```text
Ctrl + C
```

Then restart it:

```bash
uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001
```

After the backend starts again, send another request with the same `thread_id`:

```bash
curl -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "checkpoint_demo_thread_001",
    "message": "服务重启后，请你再说一遍我之前让你记住的测试关键词。",
    "debug": false
  }'
```

Expected behavior: the agent should still mention:

```text
checkpoint-persistence-demo
```

If this works, the normal chat thread context has been persisted and restored successfully after backend restart.

## Verification conclusion

With `DATABASE_URL` enabled, normal chat thread context is persisted into PostgreSQL checkpoint tables. After restarting the backend service, using the same `thread_id` allows the agent to recover prior conversation context.

This verifies the thread-level context persistence path:

```text
DATABASE_URL
  -> PostgreSQL checkpoint_* tables
  -> thread_id context recovery
  -> restored multi-turn agent behavior after restart
```

## Notes

- `MEMORY_DATABASE_URL` and `DATABASE_URL` are intentionally separate.
- `MEMORY_DATABASE_URL` is used by long-term Agent Memory and the `/memory` API.
- `DATABASE_URL` is used by checkpoint persistence for normal chat thread context.
- For first-time local deployment, especially on Windows, `DATABASE_URL` can remain disabled until checkpoint persistence needs to be tested.
- Do not commit `.env` or any real database credentials.
