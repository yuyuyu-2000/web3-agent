# ChainCloud Agent Web Console + PostgreSQL Memory Local Demo

This guide explains how to run the ChainCloud Agent backend, PostgreSQL-backed memory, and the React web console locally.

## 1. What this demo verifies

This local demo verifies the following user-level workflow:

1. PostgreSQL stores long-term Agent Memory records.
2. The backend reads and writes memory through `/memory` APIs.
3. `/chat` can receive a `memory_key` and inject the corresponding PostgreSQL-backed memory into the Agent context.
4. The web console can display memory records, select a `memory_key`, send chat messages, and show Agent responses.

## 2. Prerequisites

Required tools:

- Python environment managed by `uv`
- Node.js and npm
- PostgreSQL
- VSCode SQLTools is optional, but useful for database visualization

## 3. Start PostgreSQL

On macOS with Homebrew:

```bash
brew install postgresql@16
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
brew services start postgresql@16
pg_isready
```

Expected result:

```text
/tmp:5432 - accepting connections
```

or:

```text
localhost:5432 - accepting connections
```

## 4. Create the local database and memory table

From the repository root:

```bash
createdb chaincloud_memory_dev
psql chaincloud_memory_dev -f docs/sql/init_memory_tables.sql
```

Verify the table:

```bash
psql chaincloud_memory_dev -c "\\dt"
```

Expected table:

```text
agent_memories
```

## 5. Configure backend `.env`

Copy the example file if `.env` does not exist:

```bash
cp .env.example .env
```

Then configure the required model/API settings and memory persistence settings.

Example local PostgreSQL memory configuration:

```env
MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://<local_user>@localhost:5432/chaincloud_memory_dev
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

If your local PostgreSQL user requires a password:

```env
MEMORY_DATABASE_URL=postgresql://<local_user>:<local_password>@localhost:5432/chaincloud_memory_dev
```

For example:

```env
MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://wly:chaincloud_dev@localhost:5432/chaincloud_memory_dev
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

Do not commit `.env`.

## 6. Start the backend

From the repository root:

```bash
uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001
```

Backend URL:

```text
http://127.0.0.1:8001
```

## 7. Create a demo memory

Open another terminal and run:

```bash
curl -X POST http://127.0.0.1:8001/memory \
  -H "Content-Type: application/json" \
  -d '{
    "memory_key": "frontend_demo_memory",
    "summary": "用户正在体验 ChainCloud Agent 的前端页面与 PostgreSQL 持久化记忆功能，重点验证记忆能被保存、读取，并用于后续 Agent 对话。",
    "source_thread_id": "frontend_demo_thread_001",
    "metadata": {
      "source": "frontend_demo",
      "environment": "local_postgres",
      "scenario": "agent_web_memory_demo"
    }
  }'
```

Verify memory readback:

```bash
curl http://127.0.0.1:8001/memory/frontend_demo_memory
```

Expected result: the response should contain `memory_key=frontend_demo_memory` and the summary above.

## 8. Verify that `/chat` can use PostgreSQL memory

```bash
curl -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "frontend_demo_chat_thread_001",
    "message": "请根据你已知的长期记忆，说明我现在正在体验什么功能？",
    "memory_key": "frontend_demo_memory",
    "debug": false
  }'
```

Expected behavior: the Agent should answer based on the PostgreSQL-backed memory and mention that the user is experiencing ChainCloud Agent's web console and PostgreSQL persistent memory feature.

## 9. Visualize memory records in VSCode SQLTools

Connect to the local PostgreSQL database with SQLTools:

```text
Connection name: chaincloud_memory_dev
Server: localhost
Port: 5432
Database: chaincloud_memory_dev
Username: your local PostgreSQL username
```

Run this SQL query:

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

Expected result: the table should include `frontend_demo_memory`.

## 10. Start the frontend manually

From the repository root:

```bash
cd frontend/chaincloud-agent-web
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

## 11. Start the full local demo with the macOS launcher

After `.env` and PostgreSQL are configured, you can also start the full local demo with:

```bash
chmod +x scripts/start_chaincloud_agent_web.command
open scripts/start_chaincloud_agent_web.command
```

This opens two Terminal windows:

- backend on `http://127.0.0.1:8001`
- frontend on `http://127.0.0.1:5173`

Then the browser opens the frontend automatically.

## 12. User-level frontend demo

In the web console:

1. Refresh the memory list.
2. Select `frontend_demo_memory`.
3. Ask:

```text
请根据你的长期记忆，说说我现在正在体验什么功能？
```

Expected result: the frontend displays an Agent response based on the PostgreSQL-backed memory.

## 13. Migration to company PostgreSQL

No code change is required.

After receiving company database credentials, update `.env`:

```env
MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://user:password@host:5432/database
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

Then run the same table initialization script:

```bash
psql "$MEMORY_DATABASE_URL" -f docs/sql/init_memory_tables.sql
```

After that, start the backend normally and verify `/memory` and `/chat` with `memory_key`.

## 14. Notes

- `.env` and `.env.local` should not be committed.
- `node_modules` and `dist` should not be committed.
- This is a local demo deployment. Production deployment should use managed process supervision and secure authentication.
