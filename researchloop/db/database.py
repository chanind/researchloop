"""Async SQLite database wrapper using aiosqlite with WAL mode."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import aiosqlite


class Database:
    """Lightweight async wrapper around aiosqlite.

    Enables WAL journal mode for concurrent reads and provides
    convenience helpers for common query patterns.  On the first
    call to :meth:`connect`, the schema migrations are executed
    automatically.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    # -- lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        """Open the database connection, enable WAL mode, and run migrations."""
        if self._conn is not None:
            return

        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row

        # Enable WAL for better concurrent read performance.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # Enforce foreign-key constraints.
        await self._conn.execute("PRAGMA foreign_keys=ON")

        # Auto-initialize schema on first connect.
        from .migrations import run_migrations

        await run_migrations(self)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- async context manager ------------------------------------------------

    async def __aenter__(self) -> Database:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    # -- query helpers --------------------------------------------------------

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Cursor:
        """Execute a single SQL statement and commit."""
        assert self._conn is not None, "Database is not connected"
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor

    async def fetch_one(
        self, sql: str, params: Sequence[Any] = ()
    ) -> dict[str, Any] | None:
        """Execute *sql* and return the first row as a dict, or ``None``."""
        assert self._conn is not None, "Database is not connected"
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(
        self, sql: str, params: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        """Execute *sql* and return all rows as a list of dicts."""
        assert self._conn is not None, "Database is not connected"
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
