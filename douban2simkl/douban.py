"""Douban crawler with automatic browser cookie detection and offline archive loader."""

import json
import logging
import math
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional
import requests
from bs4 import BeautifulSoup

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None

logger = logging.getLogger("douban2simkl.douban")

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

URL_MINE = "https://m.douban.com/mine/"
URL_INTERESTS_TOTAL = "https://m.douban.com/rexxar/api/v2/user/{uid}/interests?ck={ck}&count=1&for_mobile=1"
URL_INTERESTS = (
    "https://m.douban.com/rexxar/api/v2/user/{uid}/interests"
    "?type={type}&status={status}&start={start}&count={count}&ck={ck}&for_mobile=1"
)
PAGE_SIZE = 50

STATUS_MAPPING = {
    "done": "done",
    "mark": "mark",
    "doing": "doing",
    "看过": "done",
    "想看": "mark",
    "在看": "doing",
}


class DoubanClient:
    """HTTP client for Douban with cookie handling and throttling."""

    def __init__(
        self,
        cookie_jar: Optional[requests.cookies.RequestsCookieJar] = None,
        request_interval: float = 1.0,
        retries: int = 2,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": MOBILE_UA})
        if cookie_jar is not None:
            for c in cookie_jar:
                self.session.cookies.set_cookie(c)
        self.request_interval = request_interval
        self.retries = retries
        self._last_request = 0.0
        self.ck = self._read_ck()
        if not self.ck:
            raise RuntimeError("Missing 'ck' in Douban cookies. Please ensure you are logged in to Douban.")

    def _read_ck(self) -> Optional[str]:
        for c in self.session.cookies:
            if c.name == "ck":
                return c.value
        return None

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)

    def request(self, method: str, url: str, referer: str = "https://m.douban.com/", **kw: Any) -> requests.Response:
        headers = kw.pop("headers", {}) or {}
        headers.setdefault("Referer", referer)
        last_err: Optional[Exception] = None
        for _ in range(self.retries + 1):
            self._throttle()
            try:
                resp = self.session.request(method, url, headers=headers, timeout=30, **kw)
                self._last_request = time.time()
                return resp
            except requests.RequestException as e:
                last_err = e
                time.sleep(1)
        assert last_err is not None
        raise last_err

    def checkin(self) -> Dict[str, str]:
        """Verify login status and return Douban user profile info."""
        resp = self.session.get(URL_MINE, headers={"User-Agent": MOBILE_UA}, allow_redirects=True, timeout=30)
        soup = BeautifulSoup(resp.text, "lxml")
        user_el = soup.select_one("#user")
        if user_el is None or not user_el.get("value"):
            raise RuntimeError("Douban login expired or not logged in. Please log in to douban.com in your browser.")
        uid = str(user_el.get("value"))
        username = str(user_el.get("data-name") or "")
        return {"uid": uid, "username": username}

    def get_interests_count(self, uid: str, status: str) -> int:
        """Fetch total count of interests for a status."""
        url = URL_INTERESTS.format(
            uid=uid, type="movie", status=status, start=0, count=1, ck=self.ck
        )
        resp = self.request("GET", url, referer="https://m.douban.com/mine/movie")
        if resp.status_code == 200:
            return int(resp.json().get("total", 0))
        return 0

    def fetch_all_movie_interests(
        self,
        on_init: Optional[Callable[[Dict[str, int], int], None]] = None,
        on_progress: Optional[Callable[..., None]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch all movie and TV show interests (done, mark, doing) from Douban."""
        user_info = self.checkin()
        uid = user_info["uid"]
        all_items: List[Dict[str, Any]] = []

        statuses = ["done", "doing", "mark"]
        status_totals: Dict[str, int] = {}
        for st in statuses:
            try:
                status_totals[st] = self.get_interests_count(uid, st)
            except Exception:
                status_totals[st] = 0

        grand_total = sum(status_totals.values())
        if on_init:
            on_init(status_totals, grand_total)

        for status in statuses:
            start = 0
            total_in_status = status_totals.get(status, 0)
            page_count = math.ceil(total_in_status / PAGE_SIZE) if total_in_status else 1
            status_count = 0
            while start < page_count * PAGE_SIZE:
                url = URL_INTERESTS.format(
                    uid=uid, type="movie", status=status, start=start, count=PAGE_SIZE, ck=self.ck
                )
                resp = self.request("GET", url, referer="https://m.douban.com/mine/movie")
                if resp.status_code == 200:
                    data = resp.json()
                    if "total" in data:
                        total_in_status = int(data["total"])
                        page_count = math.ceil(total_in_status / PAGE_SIZE) if total_in_status else 1

                    interests = data.get("interests", [])
                    if not interests:
                        break
                    for item in interests:
                        subject = item.get("subject", {})
                        douban_id = str(subject.get("id", ""))
                        if not douban_id:
                            continue
                        rating_val = None
                        if item.get("rating") and isinstance(item["rating"], dict):
                            rating_val = item["rating"].get("value")

                        all_items.append(
                            {
                                "douban_id": douban_id,
                                "title": subject.get("title", ""),
                                "type": subject.get("type", "movie"),
                                "status": status,
                                "create_time": item.get("create_time"),
                                "official_rating": rating_val,
                                "comment": item.get("comment"),
                                "tags": item.get("tags") or [],
                                "link": subject.get("url", f"https://movie.douban.com/subject/{douban_id}/"),
                            }
                        )
                        status_count += 1

                    if on_progress:
                        try:
                            on_progress(status, status_count, total_in_status, len(all_items), grand_total)
                        except TypeError:
                            on_progress(len(all_items), total_in_status)
                    start += PAGE_SIZE
                elif resp.status_code == 500:
                    start += PAGE_SIZE
                else:
                    break

        return all_items


def get_douban_client(
    cookie_string: Optional[str] = None, browser: Optional[str] = None
) -> DoubanClient:
    """Instantiate DoubanClient from manual cookie string or local browser cookies."""
    if cookie_string:
        jar = requests.cookies.RequestsCookieJar()
        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                jar.set(k.strip(), v.strip(), domain=".douban.com", path="/")
        return DoubanClient(cookie_jar=jar)

    if browser_cookie3 is None:
        raise RuntimeError("browser_cookie3 is not installed and no cookie string was provided.")

    loaders = []
    if browser:
        loader = getattr(browser_cookie3, browser.lower(), None)
        if loader:
            loaders.append(loader)
    else:
        for name in ["chrome", "edge", "firefox", "brave", "opera", "safari", "chromium"]:
            loader = getattr(browser_cookie3, name, None)
            if loader:
                loaders.append(loader)

    for loader in loaders:
        try:
            cj = loader(domain_name="douban.com")
            if len(cj) > 0:
                client = DoubanClient(cookie_jar=cj)
                # Verify checkin works
                client.checkin()
                return client
        except Exception:
            continue

    raise RuntimeError(
        "Could not automatically read Douban cookies from local browsers. "
        "Please ensure you are logged into douban.com in Chrome or Edge, "
        "or provide cookies via DOUBAN_COOKIE."
    )


def load_from_archive_file(filepath: str) -> List[Dict[str, Any]]:
    """Load Douban records directly from an exported jsonl or json file."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    records: List[Dict[str, Any]] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            douban_id = str(item.get("douban_id", "")).strip()
            link = item.get("link", "")

            if not douban_id:
                if "movie.douban.com" not in link:
                    continue
                match = re.search(r"/subject/(\d+)", link)
                if not match:
                    continue
                douban_id = match.group(1)

            if not link and douban_id:
                link = f"https://movie.douban.com/subject/{douban_id}/"

            raw_type = item.get("type", "看过")
            if raw_type in ("done", "mark", "doing"):
                status = raw_type
                media_type = "tv" if "季" in item.get("title", "") else "movie"
            elif raw_type in STATUS_MAPPING:
                status = STATUS_MAPPING[raw_type]
                media_type = "tv" if "季" in item.get("title", "") else "movie"
            elif raw_type in ("movie", "tv"):
                media_type = raw_type
                status = item.get("status", "done")
            else:
                status = "done"
                media_type = "tv" if "季" in item.get("title", "") else "movie"

            tags_raw = item.get("tags")
            tags = tags_raw.split(",") if isinstance(tags_raw, str) else (tags_raw or [])
            rating_val = item.get("official_rating") or item.get("my_rating") or item.get("rating")

            records.append(
                {
                    "douban_id": douban_id,
                    "title": item.get("title", ""),
                    "type": media_type,
                    "status": status,
                    "create_time": item.get("create_time"),
                    "official_rating": rating_val,
                    "comment": item.get("comment"),
                    "tags": tags,
                    "link": link,
                }
            )

    return records
