#!/usr/bin/env bash
# Stock Agent — development runner.
# Runs the backend (uvicorn --reload on :8848) and the frontend (vite dev on
# :8888) concurrently. Ctrl-C (or any exit) kills both.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VENV_DIR="${PROJECT_ROOT}/.venv"
# Activate the project venv if present (so uvicorn/app deps resolve).
if [[ -f "${VENV_DIR}/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
fi

PIDS=()

cleanup() {
  echo
  echo "[dev] shutting down…"
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Backend: uvicorn with reload ---------------------------------------------- #
echo "[dev] starting backend  -> http://127.0.0.1:8848  (uvicorn --reload)"
( cd "${PROJECT_ROOT}/backend" && exec uvicorn app.main:app --reload --port 8848 ) &
PIDS+=("$!")

# Frontend: vite dev server ------------------------------------------------- #
if command -v pnpm >/dev/null 2>&1 && [[ -f "${PROJECT_ROOT}/frontend/package.json" ]]; then
  echo "[dev] starting frontend -> http://127.0.0.1:8888  (pnpm dev)"
  ( cd "${PROJECT_ROOT}/frontend" && exec pnpm dev ) &
  PIDS+=("$!")
else
  echo "[dev] pnpm/frontend not found — running backend only"
fi

# Wait for either process to exit; trap cleans up the rest.
wait -n 2>/dev/null || wait
