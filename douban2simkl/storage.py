"""SQLite storage for caching IMDb mappings and tracking sync states."""

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional, Dict, Any, Generator


class Storage:
    """Manages SQLite database for persistent caching and state tracking."""

    def __init__(self, db_path: str = "douban_cache.db") -> None:
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema if tables do not exist."""
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS imdb_cache (
                    douban_id       TEXT PRIMARY KEY,
                    imdb_id         TEXT,
                    series_imdb_id  TEXT,
                    season          INTEGER,
                    title           TEXT,
                    updated_at      REAL
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    douban_id   TEXT PRIMARY KEY,
                    status      TEXT,
                    synced_at   REAL
                );
                """
            )
            conn.commit()

    def get_imdb_mapping(self, douban_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached IMDb mapping for a given douban_id."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM imdb_cache WHERE douban_id = ?", (str(douban_id),)
            ).fetchone()
            if row:
                return dict(row)
        return None

    def save_imdb_mapping(
        self,
        douban_id: str,
        imdb_id: Optional[str],
        series_imdb_id: Optional[str] = None,
        season: Optional[int] = None,
        title: Optional[str] = None,
    ) -> None:
        """Store or update IMDb mapping in the cache."""
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO imdb_cache (douban_id, imdb_id, series_imdb_id, season, title, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(douban_id) DO UPDATE SET
                    imdb_id = excluded.imdb_id,
                    series_imdb_id = excluded.series_imdb_id,
                    season = excluded.season,
                    title = excluded.title,
                    updated_at = excluded.updated_at
                """,
                (str(douban_id), imdb_id, series_imdb_id, season, title, now),
            )
            conn.commit()

    def get_sync_status(self, douban_id: str) -> Optional[str]:
        """Get the synchronization status for a douban_id."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM sync_state WHERE douban_id = ?", (str(douban_id),)
            ).fetchone()
            if row:
                return str(row["status"])
        return None

    def mark_synced(self, douban_id: str, status: str = "synced") -> None:
        """Record sync status for a douban_id."""
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (douban_id, status, synced_at)
                VALUES (?, ?, ?)
                ON CONFLICT(douban_id) DO UPDATE SET
                    status = excluded.status,
                    synced_at = excluded.synced_at
                """,
                (str(douban_id), status, now),
            )
            conn.commit()
