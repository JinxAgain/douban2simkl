import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from douban2simkl.storage import Storage
from douban2simkl.resolver import ItemResolver, extract_season_number


def test_extract_season_number():
    assert extract_season_number("大楼里只有谋杀 第二季") == 2
    assert extract_season_number("权力的游戏 第八季") == 8
    assert extract_season_number("行尸走肉 第11季") == 11
    assert extract_season_number("Friends Season 3") == 3
    assert extract_season_number("Stranger Things S04") == 4
    assert extract_season_number("肖申克的救赎") is None
    assert extract_season_number("爱在黎明破晓前") is None


def test_fetch_douban_imdb_id_mocked():
    resolver = ItemResolver(storage=None)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<table><tr><td>IMDb</td><td>tt0111161</td></tr></table>"

    with patch.object(resolver.session, "get", return_value=mock_resp):
        imdb_id = resolver.fetch_douban_imdb_id("1292052")
        assert imdb_id == "tt0111161"


def test_resolve_series_with_tmdb():
    resolver = ItemResolver(storage=None)

    # Mock TMDb /find response
    find_resp = MagicMock()
    find_resp.status_code = 200
    find_resp.json.return_value = {
        "tv_episode_results": [{"show_id": 130011, "season_number": 2}]
    }

    # Mock TMDb /tv/{show_id}/external_ids response
    show_resp = MagicMock()
    show_resp.status_code = 200
    show_resp.json.return_value = {"imdb_id": "tt12851524"}

    with patch.object(resolver.session, "get", side_effect=[find_resp, show_resp]):
        series_id = resolver.resolve_series_imdb_id("tt15425160", tmdb_api_key="fake_tmdb_key")
        assert series_id == "tt12851524"


def test_resolve_series_with_omdb():
    resolver = ItemResolver(storage=None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Title": "Persons of Interest",
        "seriesID": "tt12851524",
        "Season": "2",
    }

    with patch.object(resolver.session, "get", return_value=mock_resp):
        series_id = resolver.resolve_series_imdb_id("tt15425160", omdb_api_key="fake_omdb_key")
        assert series_id == "tt12851524"


def test_resolve_item_caching():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = Storage(os.path.join(tmpdir, "cache.db"))
        resolver = ItemResolver(storage=storage)

        # Pre-seed cache
        storage.save_imdb_mapping(
            douban_id="1292052",
            imdb_id="tt0111161",
            series_imdb_id=None,
            season=None,
            title="肖申克的救赎",
        )

        # Resolving should hit cache without network requests
        with patch.object(resolver, "fetch_douban_imdb_id") as mock_fetch:
            res = resolver.resolve_item("1292052", "肖申克的救赎")
            assert res["imdb_id"] == "tt0111161"
            assert mock_fetch.call_count == 0
