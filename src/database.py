"""
SQLite database initialisation for the Galatiq Invoice Processing System.

Provides:
- Schema creation (inventory, vendors, audit_log, processing_state)
- Seed data (4 inventory items, 6 vendors)
- ``init_database()`` function
- ``get_connection()`` context manager
- CLI: ``python -m src.database init``
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from src.config import get_logger, get_settings

logger = get_logger("database")

# ── SQL Statements ────────────────────────────────────────────────────────

_CREATE_TABLES = """
-- Inventory items
CREATE TABLE IF NOT EXISTS inventory (
    item_name   TEXT PRIMARY KEY,
    stock       INTEGER NOT NULL,
    unit_price  DECIMAL(10, 2),
    category    TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Vendor registry
CREATE TABLE IF NOT EXISTS vendors (
    vendor_name            TEXT PRIMARY KEY,
    risk_score             DECIMAL(3, 2) CHECK (risk_score >= 0 AND risk_score <= 1),
    is_blacklisted         BOOLEAN DEFAULT 0,
    payment_method         TEXT,
    last_transaction_date  TIMESTAMP,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Processing state (must exist before audit_log FK)
CREATE TABLE IF NOT EXISTS processing_state (
    invoice_id      TEXT PRIMARY KEY,
    current_stage   TEXT NOT NULL,
    full_state_json TEXT,
    has_errors      BOOLEAN DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_state_stage   ON processing_state(current_stage);
CREATE INDEX IF NOT EXISTS idx_state_created ON processing_state(created_at);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  TEXT NOT NULL,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    agent_name  TEXT NOT NULL,
    action      TEXT NOT NULL,
    result      TEXT NOT NULL,
    reasoning   TEXT,
    error_msg   TEXT,
    duration_ms INTEGER,
    tokens_used INTEGER
);

CREATE INDEX IF NOT EXISTS idx_audit_invoice   ON audit_log(invoice_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_agent     ON audit_log(agent_name);
"""

_SEED_INVENTORY = """
INSERT OR IGNORE INTO inventory (item_name, stock, unit_price, category)
VALUES
    ('WidgetA',  15, 250.00, 'manufacturing'),
    ('WidgetB',  10, 500.00, 'manufacturing'),
    ('GadgetX',   5, 750.00, 'electronics'),
    ('FakeItem',  0, 0,      'unknown');
"""

_SEED_VENDORS = """
INSERT OR IGNORE INTO vendors
    (vendor_name, risk_score, is_blacklisted, payment_method,
     last_transaction_date, created_at, updated_at)
VALUES
    ('Widgets Inc.',              0.2,  0, 'wire_transfer', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('Gadgets Co.',               0.4,  0, 'check',         NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('Precision Parts Ltd.',      0.1,  0, 'wire_transfer', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('Acme Industrial Supplies',  0.3,  0, 'credit_card',   NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('TechParts International',   0.25, 0, 'wire_transfer', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
    ('Unknown Vendor',            0.7,  0, 'unknown',       NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
"""


# ── Connection helper ─────────────────────────────────────────────────────

@contextmanager
def get_connection(
    db_path: str | Path | None = None,
) -> Generator[sqlite3.Connection, None, None]:
    """Yield a SQLite connection with WAL mode and foreign-key enforcement.

    Args:
        db_path: Override the database path. Falls back to ``Settings.DATABASE_PATH``.

    Yields:
        sqlite3.Connection: An open database connection.
    """
    settings = get_settings()
    resolved = Path(db_path) if db_path else settings.database_path_resolved
    resolved.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(resolved),
        timeout=settings.DATABASE_TIMEOUT,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Initialisation ────────────────────────────────────────────────────────

def init_database(db_path: str | Path | None = None) -> Path:
    """Create tables and insert seed data.

    This function is *idempotent* — it uses ``CREATE TABLE IF NOT EXISTS``
    and ``INSERT OR IGNORE`` so re-running is safe.

    Args:
        db_path: Override the database path. Falls back to ``Settings.DATABASE_PATH``.

    Returns:
        Path: Resolved path to the database file.
    """
    settings = get_settings()
    resolved = Path(db_path) if db_path else settings.database_path_resolved

    print("Initializing SQLite database...")

    with get_connection(resolved) as conn:
        cursor = conn.cursor()

        # Tables & indexes
        cursor.executescript(_CREATE_TABLES)
        print("[OK] Creating tables")

        # Seed inventory
        cursor.executescript(_SEED_INVENTORY)
        row = cursor.execute("SELECT COUNT(*) FROM inventory").fetchone()
        print(f"[OK] Seeding inventory ({row[0]} items)")

        # Seed vendors
        cursor.executescript(_SEED_VENDORS)
        row = cursor.execute("SELECT COUNT(*) FROM vendors").fetchone()
        print(f"[OK] Seeding vendors ({row[0]} vendors)")

    print(f"Database ready at: {resolved}")
    logger.info("Database initialised", extra={"database_path": str(resolved)})
    return resolved


# ── CLI entry-point ───────────────────────────────────────────────────────

def _cli() -> None:
    """Minimal CLI: ``python -m src.database init``."""
    if len(sys.argv) < 2 or sys.argv[1] != "init":
        print("Usage: python -m src.database init")
        sys.exit(1)

    init_database()


if __name__ == "__main__":
    _cli()
