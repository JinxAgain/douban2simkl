import os
import tempfile
import pytest
from douban2simkl.storage import Storage


def test_storage_imdb_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        storage = Storage(db_path)

        # Initially empty
        assert storage.get_imdb_mapping("1292052") is None

        # Save mapping
        storage.save_imdb_mapping(
            douban_id="1292052",
            imdb_id="tt0111161",
            series_imdb_id=None,
            season=None,
            title="The Shawshank Redemption",
        )

        # Retrieve mapping
        mapping = storage.get_imdb_mapping("1292052")
        assert mapping is not None
        assert mapping["douban_id"] == "1292052"
        assert mapping["imdb_id"] == "tt0111161"
        assert mapping["series_imdb_id"] is None
        assert mapping["season"] is None
        assert mapping["title"] == "The Shawshank Redemption"


def test_storage_multi_season_mapping():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        storage = Storage(db_path)

        storage.save_imdb_mapping(
            douban_id="35599435",
            imdb_id="tt15425160",
            series_imdb_id="tt12851524",
            season=2,
            title="Only Murders in the Building Season 2",
        )

        mapping = storage.get_imdb_mapping("35599435")
        assert mapping is not None
        assert mapping["imdb_id"] == "tt15425160"
        assert mapping["series_imdb_id"] == "tt12851524"
        assert mapping["season"] == 2


def test_storage_sync_status():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        storage = Storage(db_path)

        # Initially no sync status
        assert storage.get_sync_status("1292052") is None

        # Mark synced
        storage.mark_synced("1292052", "synced")
        assert storage.get_sync_status("1292052") == "synced"

        # Update status
        storage.mark_synced("1292052", "skipped")
        assert storage.get_sync_status("1292052") == "skipped"


def test_storage_settings():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        storage = Storage(db_path)

        assert storage.get_setting("token") is None
        storage.set_setting("token", "secret123")
        assert storage.get_setting("token") == "secret123"
        storage.set_setting("token", "secret456")
        assert storage.get_setting("token") == "secret456"


def test_storage_tmdb_tvdb():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        storage = Storage(db_path)

        storage.save_imdb_mapping(
            douban_id="30228394",
            imdb_id="tt13972272",
            series_imdb_id=None,
            season=1,
            title="Arcane",
            tmdb_id="117954",
            tvdb_id="396612",
        )

        mapping = storage.get_imdb_mapping("30228394")
        assert mapping is not None
        assert mapping["imdb_id"] == "tt13972272"
        assert mapping["tmdb_id"] == "117954"
        assert mapping["tvdb_id"] == "396612"

        # Test partial update coalescing
        storage.save_imdb_mapping(
            douban_id="30228394",
            series_imdb_id="tt1111111",
        )
        updated = storage.get_imdb_mapping("30228394")
        assert updated["imdb_id"] == "tt13972272"
        assert updated["series_imdb_id"] == "tt1111111"
        assert updated["tmdb_id"] == "117954"
        assert updated["tvdb_id"] == "396612"


def test_storage_failed_sync_records():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        storage = Storage(db_path)

        storage.save_imdb_mapping(douban_id="101", imdb_id="tt00101", title="Failed Movie 1")
        storage.save_imdb_mapping(douban_id="102", imdb_id="tt00102", title="Synced Movie 2")

        storage.mark_synced("101", "error: 400 Bad Request")
        storage.mark_synced("102", "synced")

        failed = storage.get_failed_sync_records()
        assert len(failed) == 1
        assert failed[0]["douban_id"] == "101"
        assert failed[0]["title"] == "Failed Movie 1"
        assert "400 Bad Request" in failed[0]["status"]

