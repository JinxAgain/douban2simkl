import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set
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

    def request_pin(self) -> Dict[str, Any]:
        """Request a device PIN code for authorization (supports OAuth2 device flow & legacy PIN)."""
        # 1. Try modern OAuth2 Device Authorization (Simkl AUTH V2)
        try:
            url = f"{SIMKL_API_BASE}/oauth2/device"
            resp = self.session.post(url, json={"client_id": self.client_id}, timeout=15)
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

    def get_existing_library_ids(self) -> Set[str]:
        """Fetch all existing movie and show IDs from user library for deduplication."""
        existing_ids: Set[str] = set()
        endpoints = [
            f"{SIMKL_API_BASE}/sync/all-items/movies",
            f"{SIMKL_API_BASE}/sync/all-items/shows",
        ]

        for url in endpoints:
            try:
                resp = self.session.get(url, params={"extended": "ids_only"}, timeout=30)
                if resp.status_code != 200:
                    logger.warning("Failed to fetch library from %s: HTTP %d", url, resp.status_code)
                    continue
                data = resp.json()

                for list_key in ("movies", "shows", "anime"):
                    items = data.get(list_key, [])
                    for item in items:
                        # Item may be wrapped like {"movie": {"ids": ...}} or directly {"ids": ...}
                        target = item.get("movie") or item.get("show") or item.get("anime") or item
                        ids_dict = target.get("ids", {})
                        for _, val in ids_dict.items():
                            if val:
                                existing_ids.add(str(val).strip())
            except Exception as e:
                logger.error("Error fetching Simkl library from %s: %s", url, e)

        return existing_ids

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
        """Add batch items to Simkl watchlist status (POST /sync/add-to-list)."""
        url = f"{SIMKL_API_BASE}/sync/add-to-list"
        payload: Dict[str, Any] = {}
        if to:
            payload["to"] = to
        if movies:
            payload["movies"] = movies
        if shows:
            payload["shows"] = shows

        resp = self._post_with_rate_limit(url, payload)
        return resp.json()
