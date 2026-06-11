"""Shared pytest fixtures.

CRITICAL: this file points the backend at a throwaway SQLite DB *before* any
``app.*`` module is imported. ``app.config.Settings`` reads ``DB_PATH`` from the
environment at import time, and ``app.db`` caches the connection on first use, so
the env var MUST be set first. We do that at module import (collection time),
well before any test module's ``from app import ...`` runs.

All tests here are pure: no network, no LLM/subprocess, no real akshare/yfinance.
The only side effects are writes to the temp DB, which we wipe between tests.
"""
from __future__ import annotations

import os
import tempfile

import pytest

# --------------------------------------------------------------------------- #
# Point the backend at a throwaway DB BEFORE importing any app module.
# --------------------------------------------------------------------------- #
_TMP_DIR = tempfile.mkdtemp(prefix="stockagent-tests-")
_DB_PATH = os.path.join(_TMP_DIR, "test_stockagent.db")
os.environ["DB_PATH"] = _DB_PATH
# Defensive: make sure trading is in the safe default the paper tests will flip.
os.environ.setdefault("TRADING_MODE", "signal")
os.environ.setdefault("REQUIRE_HUMAN_APPROVAL", "true")

# Now it is safe to import app modules (they pick up the temp DB_PATH).
from app import db  # noqa: E402
from app.config import settings  # noqa: E402

# Tables we own and clear between tests.
_TABLES = (
    "watchlist", "rules", "alerts", "decisions", "positions",
    "paper_orders", "kv", "nav_history", "realized_trades",
)


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    """Create the schema once for the whole session against the temp DB."""
    assert settings.db_path == _DB_PATH, (
        f"settings.db_path={settings.db_path!r} did not pick up the temp "
        f"DB_PATH={_DB_PATH!r}; conftest import ordering is wrong"
    )
    db.init_db()
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """Wipe every table before each test for full isolation."""
    conn = db._conn()
    with db._LOCK:
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    yield


@pytest.fixture
def paper_mode(monkeypatch):
    """Put the broker into paper mode with human approval required.

    Returns the (mutable) settings object so a test can tweak further. Restored
    automatically by monkeypatch teardown.
    """
    monkeypatch.setattr(settings, "trading_mode", "paper")
    monkeypatch.setattr(settings, "require_human_approval", True)
    return settings
