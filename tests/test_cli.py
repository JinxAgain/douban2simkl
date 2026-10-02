import json
import os
import pytest
from unittest.mock import MagicMock, patch
from douban2simkl.cli import run_pipeline


def test_cli_dry_run_pipeline(tmp_path):
    # Create sample archive file
    archive_file = tmp_path / "test_archive.jsonl"
    sample_items = [
        {
            "douban_id": "1292052",
            "title": "肖申克的救赎",
            "type": "movie",
            "status": "done",
            "create_time": "2020-01-01 12:00:00",
            "rating": 5,
            "comment": "神作！",
        },
        {
            "douban_id": "1291546",
            "title": "霸王别姬",
            "type": "movie",
            "status": "done",
            "create_time": "2020-02-01 12:00:00",
            "rating": 4,
            "comment": "4.5星",
        },
    ]
    with open(archive_file, "w", encoding="utf-8") as f:
        for it in sample_items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    db_file = tmp_path / "test.db"

    # Mock DoubanResolver.resolve_item to avoid network calls
    mock_resolve = MagicMock(side_effect=[
        {"douban_id": "1292052", "imdb_id": "tt0111161", "series_imdb_id": None, "season": None, "title": "肖申克的救赎"},
        {"douban_id": "1291546", "imdb_id": "tt0106332", "series_imdb_id": None, "season": None, "title": "霸王别姬"},
    ])

    with patch("douban2simkl.cli.DoubanResolver.resolve_item", mock_resolve), \
         patch("douban2simkl.cli.DoubanResolver.batch_resolve_wikidata", return_value={}), \
         patch("douban2simkl.cli.export_full_backup", wraps=lambda records, out: len(records)), \
         patch("douban2simkl.cli.export_simkl_failed_items", return_value=0), \
         patch("douban2simkl.cli.generate_sync_report", wraps=lambda stats, out: ""):
        run_pipeline(
            input_file=str(archive_file),
            dry_run=True,
            db_path=str(db_file),
            skip_auth=True,
        )

    assert mock_resolve.call_count == 2


def test_get_or_prompt_simkl_token_interactive(tmp_path, monkeypatch):
    from douban2simkl.cli import get_or_prompt_simkl_token
    from douban2simkl.simkl import SimklClient
    from douban2simkl.storage import Storage

    monkeypatch.setattr("douban2simkl.config.SIMKL_ACCESS_TOKEN", "")
    monkeypatch.setattr("douban2simkl.config.SIMKL_CLIENT_ID", "")
    monkeypatch.setattr("douban2simkl.cli.config.SIMKL_ACCESS_TOKEN", "")
    monkeypatch.setattr("douban2simkl.cli.config.SIMKL_CLIENT_ID", "")

    monkeypatch.setattr("douban2simkl.cli.config.save_simkl_token", lambda tok: None)
    monkeypatch.setattr("douban2simkl.cli.config.save_simkl_client_id", lambda cid: None)
    monkeypatch.setattr("douban2simkl.simkl.SimklClient.verify_token", lambda self: True)

    db_file = tmp_path / "test.db"
    storage = Storage(str(db_file))
    client = SimklClient(client_id="")

    # Simulate user entering access token directly
    fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummy_test_token_string_exceeding_80_chars_for_direct_bearer_auth"
    with patch("rich.console.Console.input", return_value=fake_token):
        token = get_or_prompt_simkl_token(client, storage, dry_run=False)
        assert token == fake_token
        assert storage.get_setting("simkl_access_token") == fake_token


def test_cli_tv_multiseason_deduplication_and_error_handling(tmp_path):
    from douban2simkl.storage import Storage

    archive_file = tmp_path / "test_tv_archive.jsonl"
    db_file = tmp_path / "test_tv.db"
    storage = Storage(str(db_file))
    storage.set_setting("simkl_access_token", "test_mock_token")

    # Season 2 previously had a stale error in sync_state
    storage.mark_synced("2002", "error: 403 Client Error: Forbidden")

    items = [
        {"douban_id": "2001", "title": "黑镜 第一季", "type": "tv", "status": "done", "rating": 5},
        {"douban_id": "2002", "title": "黑镜 第二季", "type": "tv", "status": "done", "rating": 5, "comment": "精彩绝伦"},
    ]
    with open(archive_file, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    # Mock resolver: both map to series tt2085059, with season 1 and season 2
    mock_resolve = MagicMock(side_effect=[
        {"douban_id": "2001", "imdb_id": "tt2085059", "series_imdb_id": "tt2085059", "season": 1, "title": "黑镜 第一季"},
        {"douban_id": "2002", "imdb_id": "tt2290780", "series_imdb_id": "tt2085059", "season": 2, "title": "黑镜 第二季"},
    ])

    # Mock Simkl library: Only Season 1 is in Simkl, Season 2 is NOT in Simkl
    mock_lib_data = {
        "movie_ids": set(),
        "show_ids": {"tt2085059"},
        "show_seasons": {("tt2085059", 1)},
        "completed_shows": set(),
        "all_ids": {"tt2085059"},
    }

    pushed_batches = []
    def fake_sync_history(movies=None, shows=None):
        pushed_batches.append({
            "movies": [dict(m) for m in movies] if movies else None,
            "shows": [dict(s) for s in shows] if shows else None,
        })
        return {"added": {"movies": 0, "shows": 1, "episodes": 3}}

    with patch("douban2simkl.cli.DoubanResolver.resolve_item", mock_resolve), \
         patch("douban2simkl.cli.DoubanResolver.batch_resolve_wikidata", return_value={}), \
         patch("douban2simkl.cli.DoubanResolver.batch_resolve_parent_series_wikidata", return_value={}), \
         patch("time.sleep", return_value=None), \
         patch("douban2simkl.cli.SimklClient.verify_token", return_value=True), \
         patch("douban2simkl.cli.SimklClient.get_existing_library_data", return_value=mock_lib_data), \
         patch("douban2simkl.cli.SimklClient.sync_history_batch", side_effect=fake_sync_history), \
         patch("douban2simkl.cli.export_full_backup", return_value=2), \
         patch("douban2simkl.cli.export_simkl_failed_items") as mock_export_failed, \
         patch("douban2simkl.cli.generate_sync_report", return_value=""):

        run_pipeline(
            input_file=str(archive_file),
            dry_run=False,
            db_path=str(db_file),
            skip_auth=False,
            threads=1,
        )

    # Season 1 was already in Simkl (skipped)
    # Season 2 was NOT in Simkl, so it MUST be pushed!
    assert len(pushed_batches) == 1
    assert pushed_batches[0]["shows"][0]["seasons"] == [{"number": 2}]
    assert pushed_batches[0]["shows"][0]["memo"]["text"] == "[s02]: 精彩绝伦"

    # After run, Season 2's stale error is cleared and marked synced!
    assert storage.get_sync_status("2002") == "synced"


def test_cli_tv_memo_independent_sync_pass(tmp_path):
    from douban2simkl.storage import Storage

    archive_file = tmp_path / "test_tv_memo_archive.jsonl"
    db_file = tmp_path / "test_tv_memo.db"
    storage = Storage(str(db_file))
    storage.set_setting("simkl_access_token", "test_mock_token")

    # Show is already watched in Simkl for both seasons
    items = [
        {"douban_id": "3001", "title": "火线 第一季", "type": "tv", "status": "done", "comment": "神作启幕"},
        {"douban_id": "3002", "title": "火线 第二季", "type": "tv", "status": "done", "comment": "格局更宏大"},
    ]
    with open(archive_file, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    mock_resolve = MagicMock(side_effect=[
        {"douban_id": "3001", "imdb_id": "tt0306414", "series_imdb_id": "tt0306414", "season": 1, "title": "火线 第一季"},
        {"douban_id": "3002", "imdb_id": "tt0749444", "series_imdb_id": "tt0306414", "season": 2, "title": "火线 第二季"},
    ])

    # Show is already fully completed on Simkl, but memo on Simkl is empty
    mock_lib_data = {
        "movie_ids": set(),
        "show_ids": {"tt0306414"},
        "show_seasons": {("tt0306414", 1), ("tt0306414", 2)},
        "completed_shows": {"tt0306414"},
        "show_memos": {"tt0306414": ""},
        "all_ids": {"tt0306414"},
    }

    pushed_batches = []
    def fake_sync_history(movies=None, shows=None):
        pushed_batches.append({
            "movies": [dict(m) for m in movies] if movies else None,
            "shows": [dict(s) for s in shows] if shows else None,
        })
        return {"added": {"movies": 0, "shows": 0, "episodes": 0}}

    with patch("douban2simkl.cli.DoubanResolver.resolve_item", mock_resolve), \
         patch("douban2simkl.cli.DoubanResolver.batch_resolve_wikidata", return_value={}), \
         patch("douban2simkl.cli.DoubanResolver.batch_resolve_parent_series_wikidata", return_value={}), \
         patch("time.sleep", return_value=None), \
         patch("douban2simkl.cli.SimklClient.verify_token", return_value=True), \
         patch("douban2simkl.cli.SimklClient.get_existing_library_data", return_value=mock_lib_data), \
         patch("douban2simkl.cli.SimklClient.sync_history_batch", side_effect=fake_sync_history), \
         patch("douban2simkl.cli.export_full_backup", return_value=2), \
         patch("douban2simkl.cli.export_simkl_failed_items"), \
         patch("douban2simkl.cli.generate_sync_report", return_value=""):

        run_pipeline(
            input_file=str(archive_file),
            dry_run=False,
            db_path=str(db_file),
            skip_auth=False,
            threads=1,
        )

    # Watch history was already completed on Simkl, so no seasons are re-pushed.
    # BUT the memo update pass MUST push the aggregated composite memo!
    assert len(pushed_batches) == 1
    memo_payload = pushed_batches[0]["shows"][0]
    assert memo_payload["ids"]["imdb"] == "tt0306414"
    assert "seasons" not in memo_payload
    assert memo_payload["memo"]["text"] == "[s01]: 神作启幕 ; [s02]: 格局更宏大"

