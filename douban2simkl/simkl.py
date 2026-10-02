import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import requests

logger = logging.getLogger(__name__)

SIMKL_API_BASE = "https://api.simkl.com"


class SimklClient:
    """Client for Simkl REST API."""

    def __init__(self, client_id: str, access_token: Optional[str] = None) -> None:
        self.client_id = client_id
        self.access_token = access_token
        self.session = requests.Session()
        self._update_headers()

    def _update_headers(self) -> None:
        headers = {
            "Content-Type": "application/json",
            "simkl-api-key": self.client_id,
            "User-Agent": "douban2simkl/1.0",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        self.session.headers.update(headers)

    def set_access_token(self, access_token: str) -> None:
        """Update client with authorized access token."""
        self.access_token = access_token
        self._update_headers()

    def verify_token(self) -> bool:
        """Verify if current access token is valid and has write permission."""
        if not self.access_token:
            return False
        try:
            # 1. Check basic read access
            url = f"{SIMKL_API_BASE}/users/settings"
            resp = self.session.get(url, timeout=10)
            if resp.status_code != 200:
                return False

            # 2. Check media:write scope via empty sync check
            check_url = f"{SIMKL_API_BASE}/sync/history"
            check_resp = self.session.post(check_url, json={}, timeout=10)
            if check_resp.status_code == 403 and "insufficient_scope" in check_resp.text:
                logger.warning("Simkl token lacks 'media:write' scope (403 insufficient_scope).")
                return False

            return True
        except Exception:
            return False

    def request_pin(self) -> Dict[str, Any]:
        """Request a device PIN code for authorization (supports OAuth2 device flow with write scope & legacy PIN)."""
        # 1. Try modern OAuth2 Device Authorization (Simkl AUTH V2)
        try:
            url = f"{SIMKL_API_BASE}/oauth2/device"
            resp = self.session.post(
                url,
                json={"client_id": self.client_id, "scope": "media:read media:write"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._device_code = data.get("device_code")
                data["verification_url"] = data.get("verification_uri_complete") or data.get("verification_uri", "https://simkl.com/pin")
                return data
        except Exception as e:
            logger.debug("OAuth2 device request failed: %s", e)

        # 2. Fallback to legacy GET /oauth/pin
        url = f"{SIMKL_API_BASE}/oauth/pin"
        params = {"client_id": self.client_id}
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def poll_pin(self, user_code: str) -> Optional[str]:
        """Poll to check if the user has authorized the PIN code on simkl.com/pin."""
        # 1. Modern OAuth2 Device polling
        if hasattr(self, "_device_code") and self._device_code:
            url = f"{SIMKL_API_BASE}/oauth2/token"
            payload = {
                "client_id": self.client_id,
                "device_code": self._device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
            resp = self.session.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("access_token")
                if token:
                    self.set_access_token(token)
                    return token
            return None

        # 2. Fallback to legacy GET /oauth/pin/{user_code}
        url = f"{SIMKL_API_BASE}/oauth/pin/{user_code}"
        params = {"client_id": self.client_id}
        resp = self.session.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("access_token")
            if token:
                self.set_access_token(token)
                return token
        return None

    def get_existing_library_data(self) -> Dict[str, Any]:
        """Fetch detailed existing library data distinguishing movies, shows, and individual seasons.

        Returns:
            Dict containing:
                - 'movie_ids': Set[str] of all movie IDs (IMDb, TMDb, Simkl IDs in lowercase)
                - 'show_ids': Set[str] of all show root IDs in lowercase
                - 'show_seasons': Set[Tuple[str, int]] of (show_id_lowercase, season_number)
                - 'completed_shows': Set[str] of show IDs marked as completed
                - 'all_ids': Set[str] of all media IDs (union of movie and show IDs)
        """
        movie_ids: Set[str] = set()
        show_ids: Set[str] = set()
        show_seasons: Set[Tuple[str, int]] = set()
        completed_shows: Set[str] = set()

        # 1. Fetch movies
        movies_url = f"{SIMKL_API_BASE}/sync/all-items/movies"
        try:
            resp = self.session.get(movies_url, params={"extended": "ids_only"}, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                for list_key in ("movies", "anime"):
                    items = data.get(list_key, [])
                    for item in items:
                        target = item.get("movie") or item.get("anime") or item
                        ids_dict = target.get("ids", {})
                        for val in ids_dict.values():
                            if val:
                                movie_ids.add(str(val).lower().strip())
            else:
                logger.warning("Failed to fetch movie library from %s: HTTP %d", movies_url, resp.status_code)
        except Exception as e:
            logger.error("Error fetching Simkl movie library: %s", e)

        # 2. Fetch shows with full season details
        shows_url = f"{SIMKL_API_BASE}/sync/all-items/shows"
        try:
            resp = self.session.get(
                shows_url,
                params={"extended": "full", "include_all_episodes": "yes"},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("shows", [])
                for item in items:
                    show_obj = item.get("show") or item
                    ids_dict = show_obj.get("ids", {})
                    st = str(item.get("status", "")).lower()

                    current_show_ids: List[str] = []
                    for val in ids_dict.values():
                        if val:
                            v_str = str(val).lower().strip()
                            show_ids.add(v_str)
                            current_show_ids.append(v_str)
                            if st == "completed":
                                completed_shows.add(v_str)

                    seasons = item.get("seasons", [])
                    for s in seasons:
                        s_num = s.get("number")
                        if s_num is not None:
                            for sid in current_show_ids:
                                show_seasons.add((sid, int(s_num)))
            else:
                logger.warning("Failed to fetch show library from %s: HTTP %d", shows_url, resp.status_code)
        except Exception as e:
            logger.error("Error fetching Simkl show library: %s", e)

        all_ids = movie_ids.union(show_ids)
        return {
            "movie_ids": movie_ids,
            "show_ids": show_ids,
            "show_seasons": show_seasons,
            "completed_shows": completed_shows,
            "all_ids": all_ids,
        }

    def get_existing_library_ids(self) -> Set[str]:
        """Fetch all existing movie and show IDs from user library for deduplication."""
        data = self.get_existing_library_data()
        return data["all_ids"]

    def _post_with_rate_limit(self, url: str, payload: Dict[str, Any], max_retries: int = 3) -> requests.Response:
        """Execute POST request respecting Simkl's 1 POST/sec limit and handling 429 backoff."""
        if not hasattr(self, "_last_post_time"):
            self._last_post_time = 0.0

        elapsed = time.time() - self._last_post_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        for attempt in range(max_retries):
            resp = self.session.post(url, json=payload, timeout=30)
            self._last_post_time = time.time()
            if resp.status_code == 429:
                wait_seconds = int(resp.headers.get("Retry-After", 3 * (attempt + 1)))
                logger.warning("Simkl rate limit hit (429). Retrying in %ds...", wait_seconds)
                time.sleep(wait_seconds)
                continue
            resp.raise_for_status()
            return resp

        resp.raise_for_status()
        return resp

    def sync_history_batch(
        self,
        movies: Optional[List[Dict[str, Any]]] = None,
        shows: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Sync watched history batch to Simkl (POST /sync/history)."""
        url = f"{SIMKL_API_BASE}/sync/history"
        payload: Dict[str, Any] = {}
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows

        resp = self._post_with_rate_limit(url, payload)
        return resp.json()

    def add_to_list_batch(
        self,
        movies: Optional[List[Dict[str, Any]]] = None,
        shows: Optional[List[Dict[str, Any]]] = None,
        to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add batch items to Simkl watchlist status (POST /sync/add-to-list).

        Simkl requires each individual item in 'movies' and 'shows' to contain the 'to' field
        (e.g., 'plantowatch', 'watching', 'hold', 'dropped', 'completed').
        """
        url = f"{SIMKL_API_BASE}/sync/add-to-list"
        payload: Dict[str, Any] = {}
        if to:
            payload["to"] = to

        if movies:
            formatted_movies = []
            for m in movies:
                m_copy = dict(m)
                if "to" not in m_copy:
                    m_copy["to"] = to or "plantowatch"
                formatted_movies.append(m_copy)
            payload["movies"] = formatted_movies

        if shows:
            formatted_shows = []
            for s in shows:
                s_copy = dict(s)
                if "to" not in s_copy:
                    s_copy["to"] = to or "plantowatch"
                formatted_shows.append(s_copy)
            payload["shows"] = formatted_shows

        resp = self._post_with_rate_limit(url, payload)
        return resp.json()
