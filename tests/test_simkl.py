import pytest
from unittest.mock import MagicMock, patch
from douban2simkl.simkl import SimklClient


def test_request_pin():
    client = SimklClient(client_id="test_client_id")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "user_code": "ABCD-1234",
        "verification_url": "https://simkl.com/pin",
        "expires_in": 900,
        "interval": 5,
    }

    with patch.object(client.session, "get", return_value=mock_resp):
        pin_data = client.request_pin()
        assert pin_data["user_code"] == "ABCD-1234"
        assert pin_data["verification_url"] == "https://simkl.com/pin"


def test_poll_pin_success():
    client = SimklClient(client_id="test_client_id")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "mock_access_token_xyz"}

    with patch.object(client.session, "get", return_value=mock_resp):
        token = client.poll_pin("ABCD-1234")
        assert token == "mock_access_token_xyz"
        assert client.session.headers.get("Authorization") == "Bearer mock_access_token_xyz"


def test_poll_pin_pending():
    client = SimklClient(client_id="test_client_id")
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"error": "authorization_pending"}

    with patch.object(client.session, "get", return_value=mock_resp):
        token = client.poll_pin("ABCD-1234")
        assert token is None


def test_get_existing_library_ids():
    client = SimklClient(client_id="test_client_id", access_token="mock_token")

    # Mock movies response
    movies_resp = MagicMock()
    movies_resp.status_code = 200
    movies_resp.json.return_value = {
        "movies": [
            {"movie": {"title": "Shawshank", "ids": {"imdb": "tt0111161", "tmdb": 278}}}
        ]
    }

    # Mock shows response
    shows_resp = MagicMock()
    shows_resp.status_code = 200
    shows_resp.json.return_value = {
        "shows": [
            {"show": {"title": "Friends", "ids": {"imdb": "tt0108778", "tvdb": 79168}}}
        ]
    }

    # Mock anime response
    anime_resp = MagicMock()
    anime_resp.status_code = 200
    anime_resp.json.return_value = {
        "anime": [
            {"anime": {"title": "Attack on Titan S2", "ids": {"simkl": 439744, "mal": "25777"}}}
        ]
    }

    with patch.object(client.session, "get", side_effect=[movies_resp, shows_resp, anime_resp]):
        ids = client.get_existing_library_ids()
        assert "tt0111161" in ids
        assert "278" in ids
        assert "tt0108778" in ids
        assert "79168" in ids
        assert "439744" in ids
        assert "25777" in ids


def test_get_existing_library_data():
    client = SimklClient(client_id="test_client_id", access_token="mock_token")

    movies_resp = MagicMock()
    movies_resp.status_code = 200
    movies_resp.json.return_value = {
        "movies": [
            {"status": "completed", "movie": {"title": "Shawshank", "ids": {"imdb": "tt0111161", "tmdb": 278}}},
            {"status": "plantowatch", "movie": {"title": "Obsession", "ids": {"imdb": "tt37287335"}}},
        ]
    }

    shows_resp = MagicMock()
    shows_resp.status_code = 200
    shows_resp.json.return_value = {
        "shows": [
            {
                "status": "completed",
                "show": {"title": "Twin Peaks", "ids": {"imdb": "tt0098936", "tvdb": 70533}},
                "memo": {"text": "[s01]: Great ; [s02]: Classic"},
                "seasons": [{"number": 1}, {"number": 2}, {"number": 3}],
            },
            {
                "status": "watching",
                "show": {"title": "Black Mirror", "ids": {"imdb": "tt2085059"}},
                "seasons": [{"number": 1, "episodes": [{"number": 1}]}],
            },
        ]
    }

    anime_resp = MagicMock()
    anime_resp.status_code = 200
    anime_resp.json.return_value = {
        "anime": [
            {
                "status": "completed",
                "anime": {"title": "Psycho-Pass 2", "ids": {"simkl": 48928, "mal": "23281"}},
                "seasons": [{"number": 1}],
            }
        ]
    }

    with patch.object(client.session, "get", side_effect=[movies_resp, shows_resp, anime_resp]):
        data = client.get_existing_library_data()
        assert "tt0111161" in data["movie_ids"]
        assert "278" in data["movie_ids"]
        assert "tt0111161" in data["completed_movies"]
        assert "278" in data["completed_movies"]
        assert "tt37287335" in data["movie_ids"]
        assert "tt37287335" not in data["completed_movies"]
        assert "tt0098936" in data["show_ids"]
        assert "tt0098936" in data["completed_shows"]
        assert ("tt0098936", 1) in data["show_seasons"]
        assert ("tt0098936", 2) in data["show_seasons"]
        assert ("tt2085059", 1) in data["show_seasons"]
        assert ("tt2085059", 2) not in data["show_seasons"]
        assert "tt2085059" not in data["completed_shows"]
        assert "48928" in data["show_ids"]
        assert "48928" in data["completed_shows"]
        assert ("48928", 1) in data["show_seasons"]
        assert data["show_memos"].get("tt0098936") == "[s01]: Great ; [s02]: Classic"
        assert "tt0111161" in data["all_ids"]
        assert "tt2085059" in data["all_ids"]
        assert "48928" in data["all_ids"]



def test_is_season_fully_watched_and_partial_seasons():
    client = SimklClient(client_id="test_client_id", access_token="mock_token")

    # 1. Completed show -> always True
    completed_show = {"status": "completed", "seasons": [{"number": 1}]}
    assert client.is_season_fully_watched(completed_show, 1) is True

    # 2. Show where next_to_watch points to this season -> False
    partial_next_watch = {
        "status": "watching",
        "next_to_watch": "S04E03",
        "seasons": [{"number": 4, "episodes": [{"number": 1}, {"number": 2}, {"number": 4}]}],
    }
    assert client.is_season_fully_watched(partial_next_watch, 4) is False

    # 3. Show with gaps in episodes -> False
    partial_gap = {
        "status": "watching",
        "next_to_watch": "S05E01",
        "seasons": [{"number": 4, "episodes": [{"number": 1}, {"number": 2}, {"number": 4}]}],
    }
    assert client.is_season_fully_watched(partial_gap, 4) is False

    # 4. Show where episodes match total count from get_show_season_episode_counts -> True
    full_season = {
        "status": "watching",
        "next_to_watch": "S04E01",
        "show": {"ids": {"simkl": 1254370}},
        "seasons": [{"number": 1, "episodes": [{"number": 1}, {"number": 2}, {"number": 3}]}],
    }
    with patch.object(client, "get_show_season_episode_counts", return_value={1: 3, 2: 6}):
        assert client.is_season_fully_watched(full_season, 1) is True

    # 5. Show where watched count is less than total count -> False
    partial_count = {
        "status": "watching",
        "next_to_watch": None,
        "show": {"ids": {"simkl": 1254370}},
        "seasons": [{"number": 2, "episodes": [{"number": 1}, {"number": 2}]}],
    }
    with patch.object(client, "get_show_season_episode_counts", return_value={1: 3, 2: 6}):
        assert client.is_season_fully_watched(partial_count, 2) is False


def test_sync_history_batch():
    client = SimklClient(client_id="test_client_id", access_token="mock_token")
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "added": {"movies": 1, "shows": 0, "episodes": 0},
        "not_found": {"movies": [], "shows": []},
    }

    with patch.object(client.session, "post", return_value=mock_resp):
        movies = [{"ids": {"imdb": "tt0111161"}, "rating": 10}]
        res = client.sync_history_batch(movies=movies, shows=[])
        assert res["added"]["movies"] == 1


def test_add_to_list_batch():
    client = SimklClient(client_id="test_client_id", access_token="mock_token")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "added": {"movies": 1, "shows": 1},
        "not_found": {"movies": [], "shows": []},
    }

    with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
        movies = [{"ids": {"imdb": "tt0111161"}}]
        shows = [{"ids": {"imdb": "tt0903747"}, "to": "watching"}]
        res = client.add_to_list_batch(movies=movies, shows=shows, to="plantowatch")
        assert res["added"]["movies"] == 1
        assert res["added"]["shows"] == 1

        # Verify posted payload structure
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["to"] == "plantowatch"
        assert len(payload["movies"]) == 1
        assert payload["movies"][0]["to"] == "plantowatch"
        assert payload["movies"][0]["ids"]["imdb"] == "tt0111161"
        assert len(payload["shows"]) == 1
        assert payload["shows"][0]["to"] == "watching"
        assert payload["shows"][0]["ids"]["imdb"] == "tt0903747"
