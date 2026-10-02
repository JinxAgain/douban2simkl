"""SQLite storage for caching IMDb mappings and tracking sync states."""

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Optional, Dict, Any, Generator, List


class Storage:
    """Manages SQLite database for persistent caching and state tracking."""

    def __init__(self, db_path: str = "douban_cache.db") -> None:
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema if tables do not exist."""
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS imdb_cache (
                    douban_id       TEXT PRIMARY KEY,
                    imdb_id         TEXT,
                    series_imdb_id  TEXT,
                    season          INTEGER,
                    title           TEXT,
                    updated_at      REAL,
                    tmdb_id         TEXT,
                    tvdb_id         TEXT
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    douban_id   TEXT PRIMARY KEY,
                    status      TEXT,
                    synced_at   REAL
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key     TEXT PRIMARY KEY,
                    value   TEXT
                );
                """
            )
            for col in ("tmdb_id", "tvdb_id"):
                try:
                    conn.execute(f"ALTER TABLE imdb_cache ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
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
        imdb_id: Optional[str] = None,
        series_imdb_id: Optional[str] = None,
        season: Optional[int] = None,
        title: Optional[str] = None,
        tmdb_id: Optional[str] = None,
        tvdb_id: Optional[str] = None,
    ) -> None:
        """Store or update IMDb/TMDb/TVDB mapping in the cache."""
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO imdb_cache (douban_id, imdb_id, series_imdb_id, season, title, updated_at, tmdb_id, tvdb_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(douban_id) DO UPDATE SET
                    imdb_id = COALESCE(excluded.imdb_id, imdb_cache.imdb_id),
                    series_imdb_id = COALESCE(excluded.series_imdb_id, imdb_cache.series_imdb_id),
                    season = COALESCE(excluded.season, imdb_cache.season),
                    title = COALESCE(excluded.title, imdb_cache.title),
                    tmdb_id = COALESCE(excluded.tmdb_id, imdb_cache.tmdb_id),
                    tvdb_id = COALESCE(excluded.tvdb_id, imdb_cache.tvdb_id),
                    updated_at = excluded.updated_at
                """,
                (
                    str(douban_id),
                    imdb_id,
                    series_imdb_id,
                    season,
                    title,
                    now,
                    str(tmdb_id) if tmdb_id else None,
                    str(tvdb_id) if tvdb_id else None,
                ),
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

    def get_setting(self, key: str) -> Optional[str]:
        """Get an application setting by key."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (str(key),)
            ).fetchone()
            if row:
                return str(row["value"])
        return None

    def set_setting(self, key: str, value: str) -> None:
        """Store or update an application setting."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )
            conn.commit()

    def get_failed_sync_records(self) -> List[Dict[str, Any]]:
        """Retrieve all records from sync_state where status indicates an error joined with cached IMDb metadata."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT s.douban_id, s.status, s.synced_at,
                       i.title, i.imdb_id, i.series_imdb_id, i.tmdb_id, i.tvdb_id, i.season
                FROM sync_state s
                LEFT JOIN imdb_cache i ON s.douban_id = i.douban_id
                WHERE s.status LIKE 'error:%' OR s.status NOT IN ('synced', 'already_synced', 'removed')
                ORDER BY s.synced_at DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]
