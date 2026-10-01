import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional
import requests
from douban2simkl.storage import Storage

logger = logging.getLogger(__name__)

_douban_lock = threading.Lock()
_last_douban_request = 0.0


def _throttle_douban(min_interval: float = 1.5) -> None:
    global _last_douban_request
    with _douban_lock:
        now = time.time()
        elapsed = now - _last_douban_request
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_douban_request = time.time()

CHINESE_NUMS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
}

SEASON_CHINESE_PATTERN = re.compile(r"第([一二两三四五六七八九十]+)季")
SEASON_DIGIT_PATTERN = re.compile(r"第(\d+)季")
SEASON_ENGLISH_PATTERN = re.compile(r"(?:Season\s*(\d+)|S(\d{1,2}))", re.I)

DOUBAN_DESC_URL = "https://www.douban.com/doubanapp/h5/movie/{}/desc"
DOUBAN_DESKTOP_URL = "https://movie.douban.com/subject/{}/"
IMDB_DESC_PATTERN = re.compile(r"<td>IMDb</td>\s*<td>(tt\d+)</td>")
IMDB_DESKTOP_PATTERN = re.compile(r"IMDb:.*?(\btt\d+\b)")

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_HEADERS = {
    "User-Agent": "douban2simkl/1.0 (https://github.com/douban2simkl)",
    "Accept": "application/json",
}

NEODB_FETCH_URL = "https://neodb.social/api/catalog/fetch"
NEODB_HEADERS = {
    "User-Agent": "douban2simkl/1.0 (https://github.com/douban2simkl)",
    "Accept": "application/json",
}
NEODB_IMDB_PATTERN = re.compile(r"imdb\.com/title/(tt\d+)")

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://m.douban.com/movie",
}


def extract_season_number(title: str) -> Optional[int]:
    """Extract season number from a movie/TV title."""
    if not title:
        return None

    # Check Chinese season: 第X季
    match = SEASON_CHINESE_PATTERN.search(title)
    if match:
        zh_num = match.group(1)
        if zh_num in CHINESE_NUMS:
            return CHINESE_NUMS[zh_num]

    # Check digit season: 第2季
    match = SEASON_DIGIT_PATTERN.search(title)
    if match:
        return int(match.group(1))

    # Check English season: Season 2 or S02
    match = SEASON_ENGLISH_PATTERN.search(title)
    if match:
        return int(match.group(1) or match.group(2))

    return None


class ItemResolver:
    """Resolves Douban ID to IMDb ID, and handles TV show multi-season Series ID mapping."""

    def __init__(self, storage: Optional[Storage] = None, session: Optional[requests.Session] = None) -> None:
        self.storage = storage
        self.session = session or requests.Session()

    def batch_resolve_wikidata(
        self,
        douban_ids: Any,
        chunk_size: int = 100,
        on_progress: Optional[Any] = None,
    ) -> Dict[str, str]:
        """Batch resolve Douban IDs to IMDb IDs using Wikidata SPARQL Knowledge Graph."""
        resolved_map: Dict[str, str] = {}
        valid_ids = [str(did).strip() for did in douban_ids if str(did).strip().isdigit()]
        if not valid_ids:
            return resolved_map

        for i in range(0, len(valid_ids), chunk_size):
            chunk = valid_ids[i : i + chunk_size]
            values_str = " ".join(f'"{did}"' for did in chunk)
            query = f"""
            SELECT ?douban ?imdb WHERE {{
              VALUES ?douban {{ {values_str} }}
              ?item wdt:P4529 ?douban .
              ?item wdt:P345 ?imdb .
            }}
            """
            try:
                resp = self.session.post(
                    WIKIDATA_SPARQL_URL,
                    data={"query": query, "format": "json"},
                    headers=WIKIDATA_HEADERS,
                    timeout=25,
                )
                if resp.status_code == 200:
                    bindings = resp.json().get("results", {}).get("bindings", [])
                    for b in bindings:
                        did = b.get("douban", {}).get("value")
                        imdb = b.get("imdb", {}).get("value")
                        if did and imdb and imdb.startswith("tt"):
                            resolved_map[did] = imdb
                            if self.storage:
                                self.storage.save_imdb_mapping(douban_id=did, imdb_id=imdb)
                else:
                    logger.warning("Wikidata SPARQL returned HTTP %d", resp.status_code)
            except Exception as e:
                logger.warning("Wikidata SPARQL query failed: %s", e)

            if on_progress:
                try:
                    on_progress(min(i + chunk_size, len(valid_ids)), len(valid_ids), len(resolved_map))
                except Exception:
                    pass

            time.sleep(0.2)

        return resolved_map

    def fetch_neodb_imdb_id(self, douban_id: str, timeout: int = 8) -> Optional[str]:
        """Fetch IMDb ID from NeoDB public catalog API."""
        douban_url = f"https://movie.douban.com/subject/{douban_id}/"
        try:
            resp = self.session.get(
                NEODB_FETCH_URL,
                params={"url": douban_url},
                headers=NEODB_HEADERS,
                allow_redirects=True,
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                imdb = data.get("imdb")
                if imdb and isinstance(imdb, str) and imdb.startswith("tt"):
                    return imdb.strip()
                for ext in data.get("external_resources", []):
                    u = ext.get("url", "")
                    m = NEODB_IMDB_PATTERN.search(u)
                    if m:
                        return m.group(1)
            elif resp.status_code == 429:
                logger.warning("NeoDB rate limit reached (HTTP 429).")
        except Exception as e:
            logger.debug("NeoDB request failed for %s: %s", douban_id, e)
        return None

    def fetch_douban_imdb_id(self, douban_id: str, max_retries: int = 2) -> Optional[str]:
        """Fetch IMDb ID directly from Douban pages with thread-safe rate limit backoff."""
        # 1. Try mobile description page
        for attempt in range(max_retries + 1):
            _throttle_douban(1.5)
            try:
                url = DOUBAN_DESC_URL.format(douban_id)
                resp = self.session.get(url, headers=COMMON_HEADERS, timeout=12)
                if resp.status_code == 200:
                    match = IMDB_DESC_PATTERN.search(resp.text)
                    if match:
                        return match.group(1)
                    break
                elif resp.status_code in (403, 429):
                    wait_time = 3 * (attempt + 1)
                    logger.warning("Douban rate limited (HTTP %d). Backing off for %ds...", resp.status_code, wait_time)
                    time.sleep(wait_time)
                    continue
            except Exception as e:
                logger.debug("Error fetching desc page for %s: %s", douban_id, e)
                break

        # 2. Fallback to desktop subject page
        for attempt in range(max_retries + 1):
            _throttle_douban(1.5)
            try:
                url = DOUBAN_DESKTOP_URL.format(douban_id)
                resp = self.session.get(url, headers=COMMON_HEADERS, timeout=12)
                if resp.status_code == 200:
                    match = IMDB_DESKTOP_PATTERN.search(resp.text)
                    if match:
                        return match.group(1)
                    break
                elif resp.status_code in (403, 429):
                    wait_time = 3 * (attempt + 1)
                    logger.warning("Douban rate limited (HTTP %d). Backing off for %ds...", resp.status_code, wait_time)
                    time.sleep(wait_time)
                    continue
            except Exception as e:
                logger.debug("Error fetching subject page for %s: %s", douban_id, e)
                break

        return None

    def batch_resolve_parent_series_wikidata(
        self,
        episode_imdb_ids: List[str],
        chunk_size: int = 100,
        on_progress: Optional[Any] = None,
    ) -> Dict[str, str]:
        """Batch resolve TV episode/season IMDb IDs to parent Series IMDb IDs via Wikidata."""
        series_map: Dict[str, str] = {}
        valid_ids = list(set([str(x).strip() for x in episode_imdb_ids if str(x).strip().startswith("tt")]))
        if not valid_ids:
            return series_map

        for i in range(0, len(valid_ids), chunk_size):
            chunk = valid_ids[i : i + chunk_size]
            values_str = " ".join(f'"{did}"' for did in chunk)
            query = f"""
            SELECT ?ep_imdb ?series_imdb WHERE {{
              VALUES ?ep_imdb {{ {values_str} }}
              ?ep wdt:P345 ?ep_imdb .
              ?ep (wdt:P179|wdt:P361|wdt:P4969) ?series .
              ?series wdt:P345 ?series_imdb .
            }}
            """
            try:
                resp = self.session.post(
                    WIKIDATA_SPARQL_URL,
                    data={"query": query, "format": "json"},
                    headers=WIKIDATA_HEADERS,
                    timeout=25,
                )
                if resp.status_code == 200:
                    bindings = resp.json().get("results", {}).get("bindings", [])
                    for b in bindings:
                        ep = b.get("ep_imdb", {}).get("value")
                        series = b.get("series_imdb", {}).get("value")
                        if ep and series and series.startswith("tt"):
                            series_map[ep] = series
            except Exception as e:
                logger.warning("Wikidata parent series query failed: %s", e)

            if on_progress:
                try:
                    on_progress(min(i + chunk_size, len(valid_ids)), len(valid_ids), len(series_map))
                except Exception:
                    pass

            time.sleep(0.2)

        return series_map

    def resolve_series_imdb_id(
        self,
        episode_imdb_id: str,
        tmdb_api_key: Optional[str] = None,
        omdb_api_key: Optional[str] = None,
    ) -> Optional[str]:
        """Convert a TV episode/season IMDb ID to the parent Series IMDb ID."""
        if not episode_imdb_id:
            return None

        # 1. Try TMDb if API key is provided
        if tmdb_api_key:
            try:
                find_url = (
                    f"https://api.themoviedb.org/3/find/{episode_imdb_id}"
                    f"?api_key={tmdb_api_key}&external_source=imdb_id"
                )
                resp = self.session.get(find_url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    episodes = data.get("tv_episode_results", [])
                    if episodes and "show_id" in episodes[0]:
                        show_id = episodes[0]["show_id"]
                        show_url = f"https://api.themoviedb.org/3/tv/{show_id}/external_ids?api_key={tmdb_api_key}"
                        show_resp = self.session.get(show_url, timeout=10)
                        if show_resp.status_code == 200:
                            series_imdb = show_resp.json().get("imdb_id")
                            if series_imdb:
                                return series_imdb
            except Exception:
                pass

        # 2. Try OMDb if API key is provided
        if omdb_api_key:
            try:
                omdb_url = f"http://www.omdbapi.com/?i={episode_imdb_id}&apikey={omdb_api_key}"
                resp = self.session.get(omdb_url, timeout=10)
                if resp.status_code == 200:
                    series_id = resp.json().get("seriesID")
                    if series_id:
                        return series_id
            except Exception:
                pass

        # 3. Try Wikidata Knowledge Graph (free, no API key required)
        query = f"""
        SELECT ?series_imdb WHERE {{
          ?ep wdt:P345 "{episode_imdb_id}" .
          ?ep (wdt:P179|wdt:P361|wdt:P4969) ?series .
          ?series wdt:P345 ?series_imdb .
        }} LIMIT 1
        """
        try:
            resp = self.session.post(
                WIKIDATA_SPARQL_URL,
                data={"query": query, "format": "json"},
                headers=WIKIDATA_HEADERS,
                timeout=12,
            )
            if resp.status_code == 200:
                bindings = resp.json().get("results", {}).get("bindings", [])
                if bindings:
                    s_id = bindings[0].get("series_imdb", {}).get("value")
                    if s_id and s_id.startswith("tt"):
                        return s_id
        except Exception as e:
            logger.debug("Wikidata series lookup failed for %s: %s", episode_imdb_id, e)

        return None

    def resolve_item(
        self,
        douban_id: str,
        title: str,
        is_tv: bool = False,
        tmdb_api_key: Optional[str] = None,
        omdb_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a Douban item, prioritizing Wikidata and NeoDB before falling back to Douban."""
        douban_id = str(douban_id)
        season = extract_season_number(title)

        # 1. Check local SQLite cache first (including Wikidata bulk pre-fetched items)
        if self.storage:
            cached = self.storage.get_imdb_mapping(douban_id)
            if cached and cached.get("imdb_id"):
                imdb_id = cached["imdb_id"]
                cached_season = cached.get("season") if cached.get("season") is not None else season
                series_imdb_id = cached.get("series_imdb_id")
                if cached_season and cached_season > 1 and not series_imdb_id:
                    series_imdb_id = self.resolve_series_imdb_id(
                        imdb_id, tmdb_api_key=tmdb_api_key, omdb_api_key=omdb_api_key
                    )
                    self.storage.save_imdb_mapping(
                        douban_id=douban_id,
                        imdb_id=imdb_id,
                        series_imdb_id=series_imdb_id,
                        season=cached_season,
                        title=title,
                    )
                return {
                    "douban_id": douban_id,
                    "imdb_id": imdb_id,
                    "series_imdb_id": series_imdb_id,
                    "season": cached_season,
                    "title": title or cached.get("title"),
                }

        imdb_id = None

        # 2. Query NeoDB public catalog
        try:
            imdb_id = self.fetch_neodb_imdb_id(douban_id)
            if imdb_id:
                logger.debug("Resolved %s (%s) via NeoDB: %s", douban_id, title, imdb_id)
        except Exception as e:
            logger.debug("NeoDB resolution failed for %s: %s", douban_id, e)

        # 3. Final Fallback: Douban scraping only if neither Wikidata nor NeoDB has it
        if not imdb_id:
            try:
                imdb_id = self.fetch_douban_imdb_id(douban_id)
                if imdb_id:
                    logger.debug("Resolved %s (%s) via Douban fallback: %s", douban_id, title, imdb_id)
            except Exception as e:
                logger.debug("Douban fallback resolution failed for %s: %s", douban_id, e)

        series_imdb_id = None
        if imdb_id and (is_tv or season is not None):
            if season and season > 1:
                series_imdb_id = self.resolve_series_imdb_id(
                    imdb_id, tmdb_api_key=tmdb_api_key, omdb_api_key=omdb_api_key
                )

        result = {
            "douban_id": douban_id,
            "imdb_id": imdb_id,
            "series_imdb_id": series_imdb_id,
            "season": season,
            "title": title,
        }

        # Save to cache
        if self.storage and imdb_id:
            self.storage.save_imdb_mapping(
                douban_id=douban_id,
                imdb_id=imdb_id,
                series_imdb_id=series_imdb_id,
                season=season,
                title=title,
            )

        return result


DoubanResolver = ItemResolver
