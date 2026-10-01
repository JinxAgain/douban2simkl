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
         patch("douban2simkl.cli.export_full_backup", wraps=lambda records, out: len(records)), \
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

    db_file = tmp_path / "test.db"
    storage = Storage(str(db_file))
    client = SimklClient(client_id="")

    # Simulate user entering access token directly
    fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummy_test_token_string_exceeding_80_chars_for_direct_bearer_auth"
    with patch("rich.console.Console.input", return_value=fake_token):
        token = get_or_prompt_simkl_token(client, storage, dry_run=False)
        assert token == fake_token
        assert storage.get_setting("simkl_access_token") == fake_token
