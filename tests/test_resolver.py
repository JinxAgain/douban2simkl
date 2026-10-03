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
    search_resp.json.return_value = {
        "Title": "Fight Club",
        "Year": "1999",
        "imdbID": "tt0137523",
        "Type": "movie",
        "Response": "True",
    }

    with patch.object(resolver.session, "get", return_value=search_resp):
        imdb_id = resolver.search_omdb_title("Fight Club", year=1999, omdb_api_key="fake_key")
        assert imdb_id == "tt0137523"


def test_search_omdb_rejects_media_type_mismatch():
    resolver = ItemResolver(storage=None)

    search_resp = MagicMock()
    search_resp.status_code = 200
    # OMDb returns a movie, but caller requested a TV series
    search_resp.json.return_value = {
        "Title": "Sherlock Holmes",
        "Year": "2010",
        "imdbID": "tt1475582",
        "Type": "movie",
        "Response": "True",
    }

    with patch.object(resolver.session, "get", return_value=search_resp):
        imdb_id = resolver.search_omdb_title("Sherlock Holmes", year=2010, is_tv=True, omdb_api_key="fake_key")
        assert imdb_id is None


def test_search_tmdb_title_bilingual_and_clean_title():
    resolver = ItemResolver(storage=None)

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {
        "results": [{
            "id": 76,
            "title": "爱在黎明破晓前",
            "original_title": "Before Sunrise",
            "release_date": "1995-01-27",
        }]
    }

    ext_resp = MagicMock()
    ext_resp.status_code = 200
    ext_resp.json.return_value = {"imdb_id": "tt0112471", "tvdb_id": None}

    with patch.object(resolver.session, "get", side_effect=[search_resp, ext_resp]):
        res = resolver.search_tmdb_title("爱在黎明破晓前 Before Sunrise", year=1995, tmdb_api_key="fake_key")
        assert res["tmdb_id"] == "76"
        assert res["imdb_id"] == "tt0112471"


def test_is_strict_tmdb_match_tv_season_air_date():
    from douban2simkl.resolver import is_strict_tmdb_match

    cand_valid = {
        "name": "行业",
        "original_name": "Industry",
        "first_air_date": "2020-11-09",
    }
    # Season 2 (2022) with parent series first air date 2020: valid
    assert is_strict_tmdb_match(cand_valid, "行业 第二季", expected_year=2022, is_tv=True, season=2)

    cand_future = {
        "name": "行业",
        "original_name": "Industry",
        "first_air_date": "2025-01-01",
    }
    # Series first air date in 2025 cannot be parent of a 2022 season: rejected
    assert not is_strict_tmdb_match(cand_future, "行业 第二季", expected_year=2022, is_tv=True, season=2)


def test_is_strict_tmdb_match_media_type_rejection():
    from douban2simkl.resolver import is_strict_tmdb_match

    # Searching for TV series, but candidate is a movie
    cand_movie = {
        "title": "流人",
        "original_title": "Slow Horses",
        "media_type": "movie",
        "release_date": "2022-04-01",
    }
    assert not is_strict_tmdb_match(cand_movie, "流人", is_tv=True)

    # Searching for movie, but candidate is a TV show
    cand_tv = {
        "name": "流人",
        "original_name": "Slow Horses",
        "media_type": "tv",
        "first_air_date": "2022-04-01",
    }
    assert not is_strict_tmdb_match(cand_tv, "流人", is_tv=False)


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


def test_resolve_series_metadata_tmdb_tv_season_results():
    resolver = ItemResolver(storage=None)

    find_resp = MagicMock()
    find_resp.status_code = 200
    find_resp.json.return_value = {
        "tv_season_results": [{"show_id": 90812, "season_number": 2}]
    }

    ext_resp = MagicMock()
    ext_resp.status_code = 200
    ext_resp.json.return_value = {"imdb_id": "tt10830612", "tvdb_id": 361735}

    with patch.object(resolver.session, "get", side_effect=[find_resp, ext_resp]):
        meta = resolver.resolve_series_metadata("tt15049514", tmdb_api_key="fake_key")
        assert meta["series_imdb_id"] == "tt10830612"
        assert meta["tmdb_id"] == "90812"
        assert meta["tvdb_id"] == "361735"


def test_sibling_series_inheritance():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = Storage(os.path.join(tmpdir, "cache.db"))
        resolver = ItemResolver(storage=storage)

        # Pre-seed Season 1 with series_imdb_id in cache
        storage.save_imdb_mapping(
            douban_id="30228394",
            imdb_id="tt10830612",
            series_imdb_id="tt10830612",
            season=1,
            title="行业 第一季",
            tmdb_id="90812",
            tvdb_id="361735",
        )

        # Pre-seed Season 2 without series_imdb_id
        storage.save_imdb_mapping(
            douban_id="35265497",
            imdb_id="tt15049514",
            series_imdb_id=None,
            season=2,
            title="行业 第二季",
        )

        # Resolving Season 2 with no external API keys should inherit from Season 1
        res = resolver.resolve_item("35265497", "行业 第二季")
        assert res["series_imdb_id"] == "tt10830612"
        assert res["tmdb_id"] == "90812"
        assert res["tvdb_id"] == "361735"


def test_get_season_episode_count():
    resolver = ItemResolver(storage=None)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "seasons": [
            {"season_number": 1, "episode_count": 25},
            {"season_number": 2, "episode_count": 12},
        ]
    }

    with patch.object(resolver.session, "get", return_value=mock_resp):
        cnt_s2 = resolver.get_season_episode_count("1429", 2, tmdb_api_key="test_key")
        assert cnt_s2 == 12
        cnt_s1 = resolver.get_season_episode_count("1429", 1, tmdb_api_key="test_key")
        assert cnt_s1 == 25
        # Verify cached (no second API call)
        assert resolver.get_season_episode_count("1429", 3, tmdb_api_key="test_key") is None



