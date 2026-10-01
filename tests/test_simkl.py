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

    with patch.object(client.session, "get", side_effect=[movies_resp, shows_resp]):
        ids = client.get_existing_library_ids()
        assert "tt0111161" in ids
        assert "278" in ids
        assert "tt0108778" in ids
        assert "79168" in ids


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
