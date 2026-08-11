#!/bin/bash

set -e

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$BASE_DIR"
FRONTEND_DIR="$BASE_DIR/frontend/chaincloud-agent-web"

echo "ChainCloud Agent local launcher"
echo "Backend:  $BACKEND_DIR"
echo "Frontend: $FRONTEND_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is not installed or not in PATH."
  echo "Install uv first: https://docs.astral.sh/uv/"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is not installed or not in PATH."
  echo "Install Node.js first."
  exit 1
fi

if [ ! -f "$BACKEND_DIR/.env" ]; then
  echo "WARNING: .env not found in backend root."
  echo "Please copy .env.example to .env and configure API keys and database settings."
fi

if [ ! -d "$FRONTEND_DIR" ]; then
  echo "ERROR: frontend directory not found: $FRONTEND_DIR"
  exit 1
fi

BACKEND_CMD="cd '$BACKEND_DIR' && uv run uvicorn chaincloud_agent_service.main:app --host 0.0.0.0 --port 8001"
FRONTEND_CMD="cd '$FRONTEND_DIR' && if [ ! -d node_modules ]; then npm install; fi && npm run dev"

echo "Starting backend on http://127.0.0.1:8001 ..."
osascript -e "tell application \"Terminal\" to do script \"$BACKEND_CMD\""

echo "Starting frontend on http://127.0.0.1:5173 ..."
osascript -e "tell application \"Terminal\" to do script \"$FRONTEND_CMD\""

sleep 4
open "http://127.0.0.1:5173"
