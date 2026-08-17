# Local PostgreSQL Verification for Agent Memory

> 启用自动语义召回前，请执行 `docs/sql/migrate_memory_semantic_recall.sql`。
> 迁移只增加可空列，旧记录仍可通过 `memory_key` 显式读取；旧记录的 embedding
> 可以后续批量补建。pgvector 不可用时，自动召回降级，不影响聊天主流程。

This guide describes how to verify the PostgreSQL-backed Agent Memory feature in a local development environment.

## 1. Goal

The Agent Memory module supports two storage backends:

- `memory`: in-process memory store, useful for local development and unit tests. Data is lost after service restart.
- `postgres`: PostgreSQL-backed persistent memory store. Data remains available after service restart.

This document verifies the following chain:

```text
.env
  -> load_settings()
  -> create_memory_store(settings)
  -> PostgresMemoryStore
  -> agent_memories table
  -> /memory API
  -> /chat memory_key injection
```

After this verification passes locally, migration to a shared or company PostgreSQL database only requires replacing `MEMORY_DATABASE_URL` and running the same table initialization SQL.

## 2. Install and Start Local PostgreSQL

On macOS with Homebrew:

```bash
brew install postgresql@16

echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

brew services start postgresql@16
```

Check PostgreSQL tools and service status:

```bash
psql --version
pg_isready
```

Expected result:

```text
psql (PostgreSQL) 16.x
localhost:5432 - accepting connections
```

## 3. Create Local Development Database

Create a local database for Agent Memory testing:

```bash
createdb chaincloud_memory_dev
```

Initialize the memory table:

```bash
psql chaincloud_memory_dev -f docs/sql/init_memory_tables.sql
```

Expected output:

```text
CREATE TABLE
CREATE INDEX
CREATE INDEX
```

Check the table:

```bash
psql chaincloud_memory_dev
```

Inside `psql`:

```sql
\dt
\d agent_memories
\q
```

Expected table:

```text
public | agent_memories | table | <local_user>
```

## 4. Table Schema

The table used by the PostgreSQL memory backend is `agent_memories`.

```sql
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
```

Field meanings:

| Field | Meaning |
| --- | --- |
| `memory_key` | Unique key for a memory record. `/chat` uses this key to retrieve memory. |
| `summary` | The memory content injected into the agent context. |
| `source_thread_id` | The source conversation or test thread that produced the memory. |
| `metadata` | JSONB field for extensible metadata, such as source, environment, user id, or tags. |
| `created_at` | Database creation timestamp. Useful for auditing and debugging. |
| `updated_at` | Last update timestamp. Memory list results are ordered by this field. |

## 5. Configure Local `.env`

Open the project `.env` file:

```bash
code .env
```

Add or update the memory configuration:

```env
MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://<local_user>:<local_password>@localhost:5432/chaincloud_memory_dev
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

For example, if the local PostgreSQL user is `wly` and the local password is `chaincloud_dev`:

```env
MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://wly:chaincloud_dev@localhost:5432/chaincloud_memory_dev
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

If local peer authentication is enabled, this may also work:

```env
MEMORY_DATABASE_URL=postgresql://wly@localhost:5432/chaincloud_memory_dev
```

Do not commit `.env` to GitHub.

## 6. Optional: Set Local PostgreSQL Password

If VSCode SQLTools or the application requires a password, set one for the local PostgreSQL user.

```bash
psql chaincloud_memory_dev
```

Inside `psql`:

```sql
ALTER USER <local_user> WITH PASSWORD 'chaincloud_dev';
\q
```

Example:

```sql
ALTER USER wly WITH PASSWORD 'chaincloud_dev';
```

## 7. Verify Settings Are Loaded Correctly

Run:

```bash
uv run python - <<'PY'
from chaincloud_agent_service.config import load_settings

settings = load_settings()

print("memory_store_backend =", settings.memory_store_backend)
print("memory_database_url =", settings.memory_database_url)
print("memory_postgres_table =", settings.memory_postgres_table)
print("memory_postgres_auto_create =", settings.memory_postgres_auto_create)
PY
```

Expected result:

```text
memory_store_backend = postgres
memory_database_url = postgresql://<local_user>:<local_password>@localhost:5432/chaincloud_memory_dev
memory_postgres_table = agent_memories
memory_postgres_auto_create = False
```

## 8. Verify Store-Level PostgreSQL Write and Read

This test bypasses HTTP and LLM calls. It verifies only the storage layer.

```bash
uv run python - <<'PY'
from chaincloud_agent_service.config import load_settings
from chaincloud_agent_service.memory.factory import create_memory_store

settings = load_settings()
store = create_memory_store(settings)

print("store type =", type(store).__name__)

record = store.save(
    memory_key="local_pg_test",
    summary="This is a local PostgreSQL persistent memory test record.",
    source_thread_id="manual_local_test",
    metadata={
        "source": "manual_store_test",
        "environment": "local_postgres",
    },
)

print("saved =", record)
print("loaded =", store.get("local_pg_test"))
PY
```

Expected result:

```text
store type = PostgresMemoryStore
saved = ...
loaded = ...
```

Then query PostgreSQL:

```sql
SELECT
    memory_key,
    summary,
    source_thread_id,
    metadata,
    created_at,
    updated_at
FROM agent_memories
ORDER BY updated_at DESC;
```

The result should include `local_pg_test`.

## 9. Verify `/memory` API Writes to PostgreSQL

Start the backend:

```bash
uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001
```

In another terminal, create a memory record:

```bash
curl -X POST http://127.0.0.1:8001/memory \
  -H "Content-Type: application/json" \
  -d '{
    "memory_key": "user_level_pg_test",
    "summary": "User is testing ChainCloud Agent PostgreSQL persistent memory.",
    "source_thread_id": "manual_http_test",
    "metadata": {
      "source": "curl",
      "environment": "local_postgres"
    }
  }'
```

Read the memory back:

```bash
curl http://127.0.0.1:8001/memory/user_level_pg_test
```

Expected behavior: the API returns the memory record.

## 10. Verify Persistence After Service Restart

Stop the backend with `Ctrl + C`, then restart it:

```bash
uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001
```

Read the same memory again:

```bash
curl http://127.0.0.1:8001/memory/user_level_pg_test
```

Expected behavior: the record is still available after service restart. This proves the memory is stored in PostgreSQL rather than process memory.

## 11. Verify `/chat` Can Use PostgreSQL Memory

Run:

```bash
curl -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "chat_with_pg_memory_test",
    "message": "Please answer based on the known memory: what am I testing now?",
    "memory_key": "user_level_pg_test",
    "debug": false
  }'
```

Expected behavior: the agent should answer based on the persistent memory stored in PostgreSQL.

A successful response should mention that the user is testing ChainCloud Agent PostgreSQL persistent memory.

## 12. VSCode SQLTools Usage

Recommended VSCode extensions:

- SQLTools
- SQLTools PostgreSQL/Cockroach Driver

Connection example:

```json
{
  "ssh": "Disabled",
  "previewLimit": 50,
  "server": "localhost",
  "port": 5432,
  "driver": "PostgreSQL",
  "name": "chaincloud_memory_dev",
  "database": "chaincloud_memory_dev",
  "username": "<local_user>"
}
```

Useful local debug query:

```sql
SELECT
    memory_key,
    summary,
    source_thread_id,
    metadata,
    created_at,
    updated_at
FROM agent_memories
ORDER BY updated_at DESC;
```

## 13. Migration to Company PostgreSQL

No code change is required.

When company PostgreSQL credentials are available, update `.env`:

```env
MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://user:password@host:5432/database
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

Initialize the table in the company database:

```bash
psql "$MEMORY_DATABASE_URL" -f docs/sql/init_memory_tables.sql
```

If direct `psql` access is not allowed, ask the database owner to run `docs/sql/init_memory_tables.sql`.

After that, restart the backend and verify:

```bash
curl http://127.0.0.1:8001/memory
```

## 14. Local Verification Result Example

The following local verification has been completed:

```text
POST /memory -> success
GET /memory/user_level_pg_test -> success
POST /chat with memory_key=user_level_pg_test -> success
```

Example `/chat` response:

```text
Based on the persistent memory, the user is testing ChainCloud Agent PostgreSQL persistent memory.
```

This confirms that `/chat` can retrieve memory from PostgreSQL through `memory_key` and use it in the agent response.
