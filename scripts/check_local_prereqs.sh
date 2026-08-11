#!/usr/bin/env bash
set -euo pipefail

echo "== ChainCloud-AI local prerequisite check =="

check_cmd() {
  local name="$1"
  local cmd="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "OK: $name found: $(command -v "$cmd")"
    return 0
  else
    echo "MISSING: $name ($cmd)"
    return 1
  fi
}

version_ge() {
  python3 - "$1" "$2" <<'PY'
import re
import sys

def parse(v):
    parts = re.findall(r"\d+", v)[:3]
    parts += ["0"] * (3 - len(parts))
    return tuple(map(int, parts))

raise SystemExit(0 if parse(sys.argv[1]) >= parse(sys.argv[2]) else 1)
PY
}

version_between_py() {
  python3 - "$1" <<'PY'
import re
import sys

def parse(v):
    parts = re.findall(r"\d+", v)[:3]
    parts += ["0"] * (3 - len(parts))
    return tuple(map(int, parts))

v = parse(sys.argv[1])
raise SystemExit(0 if (3, 11, 0) <= v < (3, 14, 0) else 1)
PY
}

HAS_UV=0
HAS_NODE=0
HAS_DOCKER=0

check_cmd "uv" "uv" && HAS_UV=1 || true
check_cmd "node" "node" && HAS_NODE=1 || true
check_cmd "npm" "npm" || true
check_cmd "docker" "docker" && HAS_DOCKER=1 || true

if [ "$HAS_NODE" = "1" ]; then
  NODE_VERSION="$(node --version)"
  if version_ge "$NODE_VERSION" "20.19.0"; then
    echo "OK: Node.js version is $NODE_VERSION"
  else
    echo "WARN: Node.js version is $NODE_VERSION. Recommended: Node.js 22 LTS; minimum: 20.19+."
  fi
fi

if [ "$HAS_UV" = "1" ]; then
  PY_VERSION="$(uv run python --version 2>/dev/null || true)"
  if [ -n "$PY_VERSION" ]; then
    if version_between_py "$PY_VERSION"; then
      echo "OK: uv Python version is $PY_VERSION"
    else
      echo "WARN: uv Python version is $PY_VERSION. Recommended: Python 3.11 or 3.12."
    fi
  fi
fi

if [ "$HAS_DOCKER" = "1" ]; then
  if docker compose version >/dev/null 2>&1; then
    echo "OK: docker compose found"
  else
    echo "MISSING: docker compose"
  fi

  if docker info >/dev/null 2>&1; then
    echo "OK: Docker daemon is running"
  else
    echo "ERROR: Docker daemon is not running. Start Docker Desktop first, then retry."
  fi
fi

if command -v psql >/dev/null 2>&1; then
  echo "OK: psql found: $(command -v psql)"
else
  echo "WARN: psql not found. Docker-based setup can still initialize PostgreSQL through docker compose."
fi

if [ -f ".env" ]; then
  echo "OK: .env exists"
else
  echo "MISSING: .env. Copy .env.docker.example or .env.example to .env and fill required settings."
fi

if [ -f "docs/sql/init_memory_tables.sql" ]; then
  echo "OK: docs/sql/init_memory_tables.sql exists"
else
  echo "MISSING: docs/sql/init_memory_tables.sql"
fi

if [ -d "frontend/chaincloud-agent-web" ]; then
  echo "OK: frontend/chaincloud-agent-web exists"
else
  echo "MISSING: frontend/chaincloud-agent-web"
fi

echo
echo "Port checks:"
for port in 15432 8001 5173; do
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "INFO: port $port is already in use."
  else
    echo "OK: port $port is available."
  fi
done
