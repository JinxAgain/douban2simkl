import json
import os
import pytest
from douban2simkl.exporter import (
    export_full_backup,
    export_long_reviews,
    generate_sync_report,
)


def test_export_full_backup(tmp_path):
    output_file = tmp_path / "douban_full_backup.jsonl"
    records = [
        {
            "douban_id": "1292052",
            "title": "肖申克的救赎",
            "year": 1994,
            "imdb_id": "tt0111161",
            "calibrated_rating": 10,
            "comment": "Great movie!",
            "simkl_sync_status": "synced",
        },
        {
            "douban_id": "1291546",
            "title": "霸王别姬",
            "year": 1993,
            "imdb_id": "tt0106332",
            "calibrated_rating": 10,
            "comment": "Masterpiece",
            "simkl_sync_status": "synced",
        },
    ]

    count = export_full_backup(records, str(output_file))
    assert count == 2
    assert os.path.exists(output_file)

    with open(output_file, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert len(lines) == 2
    assert lines[0]["title"] == "肖申克的救赎"
    assert lines[1]["imdb_id"] == "tt0106332"


def test_export_long_reviews(tmp_path):
    output_file = tmp_path / "long_reviews_archive.md"
    reviews = [
        {
            "douban_id": "1292052",
            "title": "肖申克的救赎",
            "year": 1994,
            "imdb_id": "tt0111161",
            "calibrated_rating": 10,
            "official_rating": 5,
            "create_time": "2020-01-01",
            "comment": "A" * 200,
        }
    ]

    count = export_long_reviews(reviews, str(output_file))
    assert count == 1
    assert os.path.exists(output_file)

    content = output_file.read_text(encoding="utf-8")
    assert "肖申克的救赎" in content
    assert "tt0111161" in content
    assert "A" * 200 in content


def test_generate_sync_report(tmp_path):
    output_file = tmp_path / "sync_report.md"
    stats = {
        "total_scanned": 100,
        "already_in_simkl": 60,
        "new_to_sync": 40,
        "synced": 38,
        "failed": 2,
        "long_reviews_count": 5,
    }

    report = generate_sync_report(stats, str(output_file))
    assert "Douban to Simkl Sync Report" in report
    assert "**Total Scanned**: 100" in report
    assert os.path.exists(output_file)


def test_export_unresolved_items(tmp_path):
    from douban2simkl.exporter import export_unresolved_items

    output_file = tmp_path / "unresolved_items.md"
    records = [
        {
            "douban_id": "99999999",
            "title": "未知短片",
            "year": 2024,
            "type": "movie",
            "status": "done",
            "imdb_id": None,
        }
    ]

    count = export_unresolved_items(records, str(output_file))
    assert count == 1
    assert os.path.exists(output_file)
    content = output_file.read_text(encoding="utf-8")
    assert "未知短片" in content
    assert "99999999" in content

