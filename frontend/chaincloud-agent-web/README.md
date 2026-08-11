# ChainCloud Agent Web Console

React + Vite web console for ChainCloud Agent.

## Features

- Chat with backend `/chat`
- Configure `thread_id`
- Select `profile`
- Enter or select `memory_key`
- Refresh PostgreSQL-backed memory list through backend `/memory`
- Use memory in agent chat responses
- Toggle debug trace

## Start backend first

From repository root:

```bash
uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001
```

Backend URL:

```text
http://127.0.0.1:8001
```

## Install frontend dependencies

```bash
npm install
```

## Run frontend

```bash
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

## Optional auth token

If the backend requires an API token, create `.env.local`:

```env
VITE_CHAT_API_TOKEN=your_token
```

Do not commit `.env.local`.

## Demo

1. Start PostgreSQL and backend.
2. Create or select a memory, for example `frontend_demo_memory`.
3. Start frontend.
4. Select the memory key.
5. Ask:

```text
请根据你的长期记忆，说说我现在正在体验什么功能？
```

The backend reads the memory from PostgreSQL and injects it into the agent context.

## Web Auth Login MVP

The Web Console supports a local/demo login flow backed by the backend auth API:

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`

After login, the browser stores `access_token` and user information in `localStorage`.
The current user is shown in the sidebar and header, and new `thread_id` / `memory_key`
values use the username as a prefix.

This is an MVP for local deployment and demo usage. It does not yet enforce full
per-user backend resource isolation.
