"""SQLite connection and migration support."""

from pathlib import Path
import sqlite3
from typing import List, Optional

from .config import get_settings


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def connect(database_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a configured SQLite connection with integrity and concurrency settings."""
    path = database_path or get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def apply_migrations(database_path: Optional[Path] = None) -> List[str]:
    """Apply each numbered SQL migration once, in a serialized transaction."""
    connection = connect(database_path)
    applied = []
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version TEXT PRIMARY KEY,
                   applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               )"""
        )

        migration_files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        available = {path.stem for path in migration_files}
        installed = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        unknown = installed - available
        if unknown:
            raise RuntimeError(
                "Database contains migrations unavailable in this checkout: "
                + ", ".join(sorted(unknown))
            )

        for migration in migration_files:
            if migration.stem in installed:
                continue
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8")
            )
            connection.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)", (migration.stem,)
            )
            connection.commit()
            applied.append(migration.stem)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
    return applied
