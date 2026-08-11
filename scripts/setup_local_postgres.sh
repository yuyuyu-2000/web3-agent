#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_SERVICE="${POSTGRES_COMPOSE_SERVICE:-postgres}"

echo "== ChainCloud-AI local PostgreSQL setup =="
echo "Repository: $ROOT_DIR"

if [ ! -f "docs/sql/init_memory_tables.sql" ]; then
  echo "ERROR: docs/sql/init_memory_tables.sql not found."
  exit 1
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "Starting Docker PostgreSQL service..."
  docker compose up -d "$COMPOSE_SERVICE"

  echo "Waiting for PostgreSQL to become ready..."
  for i in $(seq 1 40); do
    if docker compose exec -T "$COMPOSE_SERVICE" pg_isready -U chaincloud -d chaincloud_memory_dev >/dev/null 2>&1; then
      echo "PostgreSQL is ready."
      break
    fi
    if [ "$i" -eq 40 ]; then
      echo "ERROR: PostgreSQL did not become ready in time."
      docker compose ps
      exit 1
    fi
    sleep 2
  done

  echo "Initializing memory table through Docker PostgreSQL..."
  docker compose exec -T "$COMPOSE_SERVICE" psql -U chaincloud -d chaincloud_memory_dev < docs/sql/init_memory_tables.sql
  if [ -f docs/sql/init_auth_tables.sql ]; then
    echo "Initializing auth user table through Docker PostgreSQL..."
    docker compose exec -T "$COMPOSE_SERVICE" psql -U chaincloud -d chaincloud_memory_dev < docs/sql/init_auth_tables.sql
  fi

  echo "Verifying tables..."
  docker compose exec -T "$COMPOSE_SERVICE" psql -U chaincloud -d chaincloud_memory_dev -c "\\dt"

  echo
  echo "Done."
  echo "Use this in .env:"
  echo "MEMORY_STORE_BACKEND=postgres"
  echo "MEMORY_DATABASE_URL=postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev"
  echo "MEMORY_POSTGRES_TABLE=agent_memories"
  echo "MEMORY_POSTGRES_AUTO_CREATE=0"
  exit 0
fi

if command -v psql >/dev/null 2>&1; then
  DB_URL="${MEMORY_DATABASE_URL:-postgresql://chaincloud:chaincloud_dev@localhost:15432/chaincloud_memory_dev}"
  echo "Docker Compose not available. Falling back to local psql."
  echo "Initializing memory table using: $DB_URL"
  psql "$DB_URL" -f docs/sql/init_memory_tables.sql
  if [ -f docs/sql/init_auth_tables.sql ]; then
    psql "$DB_URL" -f docs/sql/init_auth_tables.sql
  fi
  psql "$DB_URL" -c "\\dt"
  echo "Done."
  exit 0
fi

echo "ERROR: Neither docker compose nor psql is available."
echo "Install Docker Desktop, or install PostgreSQL client tools."
exit 1
