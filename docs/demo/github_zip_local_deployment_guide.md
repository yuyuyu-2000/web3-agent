# ChainCloud-AI Cross-Platform Local Deployment Guide

This guide explains how to run ChainCloud-AI locally on macOS or Windows after downloading the repository from GitHub, including Docker PostgreSQL, backend service, React web console, and PostgreSQL-backed long-term memory.

## 1. What this guide verifies

After following this guide, a new local user should be able to verify:

- Backend service starts on `http://127.0.0.1:8001`
- React web console starts on `http://127.0.0.1:5173`
- Docker PostgreSQL starts locally
- Long-term memory is stored in the `agent_memories` table
- `/memory` can write and read persistent memory
- `/chat` can use `memory_key` to inject PostgreSQL-backed memory into the agent response
- The frontend can refresh memory records, select a memory key, and send a chat request

## 2. Required tools

### macOS

Install:

- Homebrew
- Docker Desktop
- `uv`
- Node.js and npm. Node.js 22 LTS recommended; minimum 20.19+
- Optional: VSCode and SQLTools

Quick checks:

```bash
uv --version
node --version
npm --version
docker --version
docker compose version
```

### Windows

Install:

- Docker Desktop for Windows
- Node.js 22 LTS recommended; minimum 20.19+
- Python 3.11 or 3.12
- `uv`
- Optional: VSCode

Quick checks in PowerShell:

```powershell
uv --version
node --version
npm --version
docker --version
docker compose version
```

If Docker commands fail, open Docker Desktop first and wait until the engine is running.

## 3. Download and unzip the repository

Download from GitHub:

```text
Code -> Download ZIP
```

Example locations:

macOS:

```bash
cd ~/Desktop/Chaincloud-AI-main
```

Windows PowerShell:

```powershell
cd D:\Code\Chaincloud-AI-main
```

## 4. Check local prerequisites

macOS or Git Bash:

```bash
./scripts/check_local_prereqs.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_local_prereqs.ps1
```

## 5. Configure `.env`

Copy the Docker-ready example.

macOS:

```bash
cp .env.docker.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.docker.example .env
notepad .env
```

Fill model settings:

```env
OPENAI_API_KEY=replace_with_your_api_key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

Keep Docker PostgreSQL defaults unless there is a port conflict:

```env
POSTGRES_HOST_PORT=15432

DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev

MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

Notes:

- `DATABASE_URL` is an advanced option for normal chat thread checkpoint persistence. For the first Windows local run, keep it disabled unless you have verified async PostgreSQL checkpoint compatibility.

- `MEMORY_DATABASE_URL` is for long-term Agent Memory.
- `DATABASE_URL` is for normal chat thread checkpoint persistence.
- `READONLY_DATABASE_URL` is optional and only for read-only database tools.
- Do not commit `.env`.
- Default host port is `15432` to avoid conflicts with a local PostgreSQL on `5432`.

If you want to use host port `5432`, update all three values:

```env
POSTGRES_HOST_PORT=5432
DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:5432/chaincloud_memory_dev
MEMORY_DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:5432/chaincloud_memory_dev
```

## 6. Start Docker PostgreSQL and initialize memory table

### macOS or Git Bash

```bash
docker compose up -d postgres
./scripts/setup_local_postgres.sh
```

### Windows PowerShell

```powershell
docker compose up -d postgres
powershell -ExecutionPolicy Bypass -File scripts\setup_local_postgres.ps1
```

Verify table:

```powershell
docker compose exec -T postgres psql -U chaincloud -d chaincloud_memory_dev -c "\dt"
```

Expected table:

```text
agent_memories
```

## 7. Start backend

From repository root:

```bash
uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001
```

Test memory list in another terminal.

macOS or Git Bash:

```bash
curl http://127.0.0.1:8001/memory
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8001/memory"
```

## 8. Create demo memory

### macOS or Git Bash

```bash
curl -X POST http://127.0.0.1:8001/memory \
  -H "Content-Type: application/json" \
  -d '{
    "memory_key": "frontend_demo_memory",
    "summary": "用户正在体验 ChainCloud Agent 的前端页面与 PostgreSQL 持久化记忆功能，重点验证记忆能被保存、读取，并用于后续 Agent 对话。",
    "source_thread_id": "frontend_demo_thread_001",
    "metadata": {
      "source": "cross_platform_demo",
      "environment": "docker_postgres",
      "scenario": "agent_web_memory_demo"
    }
  }'
```

### Windows PowerShell

```powershell
$body = @{
  memory_key = "frontend_demo_memory"
  summary = "用户正在体验 ChainCloud Agent 的前端页面与 PostgreSQL 持久化记忆功能，重点验证记忆能被保存、读取，并用于后续 Agent 对话。"
  source_thread_id = "frontend_demo_thread_001"
  metadata = @{
    source = "cross_platform_demo"
    environment = "docker_postgres"
    scenario = "agent_web_memory_demo"
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8001/memory" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

Read it back:

macOS or Git Bash:

```bash
curl http://127.0.0.1:8001/memory/frontend_demo_memory
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8001/memory/frontend_demo_memory"
```

## 9. Verify `/chat` can use PostgreSQL memory

### macOS or Git Bash

```bash
curl -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "zip_demo_chat_thread_001",
    "message": "请根据你已知的长期记忆，说明我现在正在体验什么功能？",
    "memory_key": "frontend_demo_memory",
    "debug": false
  }'
```

### Windows PowerShell

```powershell
$chatBody = @{
  thread_id = "zip_demo_chat_thread_001"
  message = "请根据你已知的长期记忆，说明我现在正在体验什么功能？"
  memory_key = "frontend_demo_memory"
  debug = $false
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8001/chat" `
  -Method Post `
  -ContentType "application/json" `
  -Body $chatBody
```

Expected behavior: the agent should mention that the user is testing the ChainCloud Agent web console and PostgreSQL-backed persistent memory.

## 10. Start frontend

In another terminal:

macOS or Git Bash:

```bash
cd frontend/chaincloud-agent-web
npm install
npm run dev
```

Windows PowerShell:

```powershell
cd frontend\chaincloud-agent-web
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

In the web console:

1. Click refresh in the memory panel.
2. Select `frontend_demo_memory`.
3. Ask:

```text
请根据你的长期记忆，说说我现在正在体验什么功能？
```

Expected behavior: the frontend displays an agent response based on PostgreSQL-backed memory.

## 11. One-click launchers

### macOS

After `.env` and PostgreSQL are configured:

```bash
chmod +x scripts/start_chaincloud_agent_web.command
open scripts/start_chaincloud_agent_web.command
```

### Windows PowerShell

After `.env` and PostgreSQL are configured:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_chaincloud_agent_web.ps1
```

The launcher starts backend and frontend in separate terminal windows and opens the frontend page.

## 12. Company PostgreSQL migration

No code changes are required.

Update `.env`:

```env
MEMORY_STORE_BACKEND=postgres
MEMORY_DATABASE_URL=postgresql://user:password@host:5432/database
MEMORY_POSTGRES_TABLE=agent_memories
MEMORY_POSTGRES_AUTO_CREATE=0
```

Initialize the same table:

```bash
psql "$MEMORY_DATABASE_URL" -f docs/sql/init_memory_tables.sql
```

If you also want normal chat thread checkpoint persistence, configure:

```env
DATABASE_URL=postgresql://user:password@host:5432/database
```

If the company provides a read-only database account, use it only for:

```env
READONLY_DATABASE_URL=postgresql://readonly_user:password@host:5432/database
```

Long-term memory requires a writable PostgreSQL account.

## 13. Troubleshooting

### Docker command not found on macOS

Docker Desktop may be installed, but its CLI is not in `PATH`.

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker --version
docker compose version
```

To persist it:

```bash
grep -qxF 'export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"' ~/.zshrc \
  || echo 'export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### PostgreSQL port conflict

Default Docker host port is `15432`.

If `15432` is occupied, update `.env`:

```env
POSTGRES_HOST_PORT=15433
DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15433/chaincloud_memory_dev
MEMORY_DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15433/chaincloud_memory_dev
```

Then restart:

```bash
docker compose down
docker compose up -d postgres
```

### `/memory` works but `/chat` fails

Check model API settings in `.env`:

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL=
```

### Frontend memory list is empty

Create a memory record through `POST /memory`, then refresh the frontend memory list.

### Do not commit secrets

Never commit:

- `.env`
- `.env.local`
- real API keys
- real company PostgreSQL credentials


### Frontend fails with Rolldown native binding error on Windows

If you see an error such as:

```text
Cannot find module '@rolldown/binding-win32-x64-msvc'
```

first upgrade Node.js. The frontend requires Node.js 20.19+ and Node.js 22 LTS is recommended.

Then clean and reinstall frontend dependencies:

```powershell
cd frontend\chaincloud-agent-web
Remove-Item -Recurse -Force node_modules
npm install
npm run dev
```

If it still fails in a ZIP test directory, remove `package-lock.json` and reinstall:

```powershell
Remove-Item -Force package-lock.json
npm install
npm run dev
```


## User login MVP

The local login MVP uses PostgreSQL table `agent_users`.

Initialize the table with:

```bash
docker compose exec -T postgres psql -U chaincloud -d chaincloud_memory_dev < docs/sql/init_auth_tables.sql
```

Local `.env` example:

```env
AUTH_DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev
AUTH_USERS_TABLE=agent_users
AUTH_POSTGRES_AUTO_CREATE=0
AUTH_TOKEN_SECRET=change-me-in-local-dev
AUTH_TOKEN_EXPIRE_MINUTES=1440
```

Basic API flow:

```bash
curl -X POST http://127.0.0.1:8001/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo-password","display_name":"Demo User"}'

curl -X POST http://127.0.0.1:8001/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo-password"}'
```

Use the returned bearer token with:

```bash
curl http://127.0.0.1:8001/auth/me \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

## Web Console login verification

After starting backend and frontend, open:

```text
http://127.0.0.1:5173
```

Verify the login MVP:

1. Register a user in the sidebar.
2. Confirm the current user is displayed.
3. Create a new chat.
4. Confirm `thread_id` and `memory_key` use the username prefix.
5. Send a chat message.
6. Summarize the current thread into memory.
7. Refresh the page and confirm the login state is restored through `/auth/me`.
8. Logout and confirm the UI returns to unauthenticated mode.
