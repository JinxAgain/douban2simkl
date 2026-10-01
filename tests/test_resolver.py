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
        with patch.object(resolver, "fetch_douban_imdb_id") as mock_fetch, \
             patch.object(resolver, "fetch_neodb_imdb_id") as mock_neodb:
            res = resolver.resolve_item("1292052", "肖申克的救赎")
            assert res["imdb_id"] == "tt0111161"
            assert mock_fetch.call_count == 0
            assert mock_neodb.call_count == 0


def test_batch_resolve_wikidata_mocked():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = Storage(os.path.join(tmpdir, "cache.db"))
        resolver = ItemResolver(storage=storage)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": {
                "bindings": [
                    {"douban": {"value": "1292052"}, "imdb": {"value": "tt0111161"}},
                    {"douban": {"value": "1291546"}, "imdb": {"value": "tt0106332"}},
                ]
            }
        }

        with patch.object(resolver.session, "post", return_value=mock_resp):
            res = resolver.batch_resolve_wikidata(["1292052", "1291546"])
            assert res["1292052"] == "tt0111161"
            assert res["1291546"] == "tt0106332"

            # Check cached in SQLite
            cached = storage.get_imdb_mapping("1292052")
            assert cached is not None
            assert cached["imdb_id"] == "tt0111161"


def test_fetch_neodb_imdb_id_mocked():
    resolver = ItemResolver(storage=None)

    # 1. Direct imdb field
    mock_resp_direct = MagicMock()
    mock_resp_direct.status_code = 200
    mock_resp_direct.json.return_value = {"imdb": "tt0111161"}

    with patch.object(resolver.session, "get", return_value=mock_resp_direct):
        imdb_id = resolver.fetch_neodb_imdb_id("1292052")
        assert imdb_id == "tt0111161"

    # 2. External resources fallback
    mock_resp_ext = MagicMock()
    mock_resp_ext.status_code = 200
    mock_resp_ext.json.return_value = {
        "imdb": None,
        "external_resources": [{"url": "https://www.imdb.com/title/tt0106332/"}],
    }

    with patch.object(resolver.session, "get", return_value=mock_resp_ext):
        imdb_id = resolver.fetch_neodb_imdb_id("1291546")
        assert imdb_id == "tt0106332"


def test_resolve_item_prioritizes_neodb_over_douban():
    resolver = ItemResolver(storage=None)

    with patch.object(resolver, "fetch_neodb_imdb_id", return_value="tt0111161") as mock_neodb, \
         patch.object(resolver, "fetch_douban_imdb_id") as mock_douban:
        res = resolver.resolve_item("1292052", "肖申克的救赎")
        assert res["imdb_id"] == "tt0111161"
        assert mock_neodb.call_count == 1
        assert mock_douban.call_count == 0


def test_resolve_series_metadata_tmdb_and_tvdb():
    resolver = ItemResolver(storage=None)

    find_resp = MagicMock()
    find_resp.status_code = 200
    find_resp.json.return_value = {
        "tv_episode_results": [{"show_id": 1399, "season_number": 2}]
    }

    ext_resp = MagicMock()
    ext_resp.status_code = 200
    ext_resp.json.return_value = {"imdb_id": "tt0944947", "tvdb_id": 121361}

    with patch.object(resolver.session, "get", side_effect=[find_resp, ext_resp]):
        meta = resolver.resolve_series_metadata("tt1480055", tmdb_api_key="fake_key")
        assert meta["series_imdb_id"] == "tt0944947"
        assert meta["tmdb_id"] == "1399"
        assert meta["tvdb_id"] == "121361"


def test_search_tmdb_title_fallback():
    resolver = ItemResolver(storage=None)

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {
        "results": [{
            "id": 550,
            "title": "搏击俱乐部",
            "original_title": "Fight Club",
            "release_date": "1999-10-15",
        }]
    }

    ext_resp = MagicMock()
    ext_resp.status_code = 200
    ext_resp.json.return_value = {"imdb_id": "tt0137523", "tvdb_id": None}

    with patch.object(resolver.session, "get", side_effect=[search_resp, ext_resp]):
        res = resolver.search_tmdb_title("搏击俱乐部", year=1999, tmdb_api_key="fake_key")
        assert res["tmdb_id"] == "550"
        assert res["imdb_id"] == "tt0137523"


def test_search_tmdb_title_rejects_unrelated_fuzzy_match():
    resolver = ItemResolver(storage=None)

    # TMDb returns an unrelated movie that matched fuzzy keywords
    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {
        "results": [{
            "id": 9999,
            "title": "完全无关的其他电影",
            "original_title": "Completely Unrelated",
            "release_date": "2015-05-01",
        }]
    }

    with patch.object(resolver.session, "get", return_value=search_resp):
        res = resolver.search_tmdb_title("小众国产纪录片", year=2021, tmdb_api_key="fake_key")
        # Must be rejected because title and year do not strictly match
        assert res["tmdb_id"] is None
        assert res["imdb_id"] is None


def test_search_omdb_title_fallback():
    resolver = ItemResolver(storage=None)

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {"Title": "Fight Club", "Year": "1999", "imdbID": "tt0137523", "Response": "True"}

    with patch.object(resolver.session, "get", return_value=search_resp):
        imdb_id = resolver.search_omdb_title("Fight Club", year=1999, omdb_api_key="fake_key")
        assert imdb_id == "tt0137523"


def test_fetch_neodb_ids_with_tmdb_tvdb():
    resolver = ItemResolver(storage=None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "imdb": "tt13972272",
        "external_resources": [
            {"url": "https://www.themoviedb.org/tv/117954/season/1"},
            {"url": "https://thetvdb.com/series/396612"},
        ],
    }

    with patch.object(resolver.session, "get", return_value=mock_resp):
        ids = resolver.fetch_neodb_ids("30228394")
        assert ids["imdb_id"] == "tt13972272"
        assert ids["tmdb_id"] == "117954"
        assert ids["tvdb_id"] == "396612"


