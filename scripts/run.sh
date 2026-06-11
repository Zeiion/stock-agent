#!/usr/bin/env bash
# Stock Agent — one-shot production-ish runner.
# Creates/activates a project-root .venv, installs Python deps, builds the
# frontend (if pnpm is available), then launches the backend (python -m app).
set -euo pipefail

# Resolve project root (scripts/ -> project root) regardless of CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# 1. Virtualenv ------------------------------------------------------------- #
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[run] creating virtualenv at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# 2. Python deps ------------------------------------------------------------ #
echo "[run] installing Python dependencies"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r "${PROJECT_ROOT}/requirements.txt"

# 3. Frontend build (optional) --------------------------------------------- #
if command -v pnpm >/dev/null 2>&1; then
  if [[ -f "${PROJECT_ROOT}/frontend/package.json" ]]; then
    echo "[run] building frontend"
    ( cd "${PROJECT_ROOT}/frontend" && pnpm install && pnpm build )
  fi
else
  echo "[run] pnpm not found — skipping frontend build (backend will serve API only)"
fi

# 4. Launch backend --------------------------------------------------------- #
echo "[run] starting backend (python -m app)"
cd "${PROJECT_ROOT}/backend"
exec python -m app
