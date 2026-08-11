# Web Auth Login MVP

This document records the frontend login MVP for ChainCloud Agent Web Console.

## Purpose

The Web Console now supports a local user login experience backed by the backend auth API:

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`

The backend stores users in PostgreSQL table `agent_users`.

## Current scope

This is an MVP for local/demo usage.

It supports:

- User registration from the frontend
- User login from the frontend
- Browser-side token storage in `localStorage`
- `/auth/me` session restoration after page refresh
- Current user display in the sidebar and header
- Logout
- User-prefixed default `thread_id`
- User-prefixed default `memory_key`
- User metadata attached when summarizing a thread into memory

It does not yet enforce per-user isolation on all backend resources.

## Local setup

Initialize PostgreSQL auth table:

```bash
docker compose exec -T postgres psql -U chaincloud -d chaincloud_memory_dev < docs/sql/init_auth_tables.sql
```

Make sure `.env` contains:

```env
AUTH_DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev
AUTH_USERS_TABLE=agent_users
AUTH_POSTGRES_AUTO_CREATE=0
AUTH_TOKEN_SECRET=change-me-in-local-dev
AUTH_TOKEN_EXPIRE_MINUTES=1440
```

Start backend:

```bash
uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001
```

Start frontend:

```bash
cd frontend/chaincloud-agent-web
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

## Manual verification

1. Register a user in the left sidebar.
2. Confirm the current user is displayed.
3. Create a new chat.
4. Confirm `thread_id` uses the username prefix.
5. Confirm `memory_key` uses the username prefix.
6. Send a chat message.
7. Summarize the thread into memory.
8. Refresh the page and confirm `/auth/me` restores the login state.
9. Logout and confirm the UI returns to unauthenticated mode.

## Database verification

```bash
docker compose exec -T postgres psql -U chaincloud -d chaincloud_memory_dev -c "SELECT username, password_hash, display_name, created_at, last_login_at FROM agent_users ORDER BY created_at DESC LIMIT 5;"
```

Expected behavior:

- User records exist in `agent_users`.
- `password_hash` starts with `pbkdf2_sha256$`.
- Plaintext passwords are not stored.
