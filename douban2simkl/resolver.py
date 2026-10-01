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
NEODB_TMDB_PATTERN = re.compile(r"themoviedb\.org/(?:tv|movie)/(\d+)")
NEODB_TVDB_PATTERN = re.compile(r"thetvdb\.com/.*?series/(\d+)")

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


def is_strict_tmdb_match(candidate: Dict[str, Any], query_title: str, expected_year: Optional[Any]) -> bool:
    """Strictly validate whether a TMDb search result exactly matches the title and year."""
    if not candidate or not query_title:
        return False

    cand_title = (candidate.get("title") or candidate.get("name") or "").strip().lower()
    cand_orig_title = (candidate.get("original_title") or candidate.get("original_name") or "").strip().lower()
    clean_query = query_title.strip().lower()

    # Normalize punctuation and whitespace for both Chinese and English titles
    norm_query = re.sub(r"[^\w\u4e00-\u9fa5]", "", clean_query)
    norm_title = re.sub(r"[^\w\u4e00-\u9fa5]", "", cand_title)
    norm_orig = re.sub(r"[^\w\u4e00-\u9fa5]", "", cand_orig_title)

    # Title must exactly match either the localized title or original title
    if norm_query != norm_title and norm_query != norm_orig:
        return False

    # Year must match within +-1 year if year is known
    if expected_year:
        try:
            exp_y = int(str(expected_year).strip()[:4])
            release_date = candidate.get("release_date") or candidate.get("first_air_date") or ""
            cand_year_str = release_date[:4]
            if cand_year_str.isdigit():
                cand_y = int(cand_year_str)
                if abs(cand_y - exp_y) > 1:
                    return False
        except (ValueError, TypeError):
            pass

    return True


class ItemResolver:
    """Resolves Douban ID to IMDb, TMDb, and TVDB IDs, and handles TV multi-season series mapping."""

    def __init__(self, storage: Optional[Storage] = None, session: Optional[requests.Session] = None) -> None:
        self.storage = storage
        self.session = session or requests.Session()

    def batch_resolve_wikidata(
        self,
        douban_ids: Any,
        chunk_size: int = 100,
        on_progress: Optional[Any] = None,
    ) -> Dict[str, str]:
        """Batch resolve Douban IDs to IMDb, TMDb, and TVDB IDs using Wikidata SPARQL Knowledge Graph."""
        resolved_map: Dict[str, str] = {}
        valid_ids = [str(did).strip() for did in douban_ids if str(did).strip().isdigit()]
        if not valid_ids:
            return resolved_map

        for i in range(0, len(valid_ids), chunk_size):
            chunk = valid_ids[i : i + chunk_size]
            values_str = " ".join(f'"{did}"' for did in chunk)
            query = f"""
            SELECT ?douban ?imdb ?tmdb_movie ?tmdb_tv ?tvdb WHERE {{
              VALUES ?douban {{ {values_str} }}
              ?item wdt:P4529 ?douban .
              OPTIONAL {{ ?item wdt:P345 ?imdb . }}
              OPTIONAL {{ ?item wdt:P4985 ?tmdb_movie . }}
              OPTIONAL {{ ?item wdt:P4983 ?tmdb_tv . }}
              OPTIONAL {{ ?item wdt:P4835 ?tvdb . }}
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
                        tmdb = b.get("tmdb_movie", {}).get("value") or b.get("tmdb_tv", {}).get("value")
                        tvdb = b.get("tvdb", {}).get("value")

                        imdb_val = imdb if (imdb and imdb.startswith("tt")) else None
                        tmdb_val = str(tmdb).strip() if tmdb else None
                        tvdb_val = str(tvdb).strip() if tvdb else None

                        if did and (imdb_val or tmdb_val or tvdb_val):
                            resolved_map[did] = imdb_val or tmdb_val or tvdb_val
                            if self.storage:
                                self.storage.save_imdb_mapping(
                                    douban_id=did,
                                    imdb_id=imdb_val,
                                    tmdb_id=tmdb_val,
                                    tvdb_id=tvdb_val,
                                )
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

    def fetch_neodb_ids(self, douban_id: str, timeout: int = 8) -> Dict[str, Optional[str]]:
        """Fetch IMDb, TMDb, and TVDB IDs from NeoDB public catalog API."""
        result: Dict[str, Optional[str]] = {"imdb_id": None, "tmdb_id": None, "tvdb_id": None}
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
                    result["imdb_id"] = imdb.strip()
                for ext in data.get("external_resources", []):
                    u = ext.get("url", "")
                    if not result["imdb_id"]:
                        m = NEODB_IMDB_PATTERN.search(u)
                        if m:
                            result["imdb_id"] = m.group(1)
                    if not result["tmdb_id"]:
                        m = NEODB_TMDB_PATTERN.search(u)
                        if m:
                            result["tmdb_id"] = m.group(1)
                    if not result["tvdb_id"]:
                        m = NEODB_TVDB_PATTERN.search(u)
                        if m:
                            result["tvdb_id"] = m.group(1)
            elif resp.status_code == 429:
                logger.warning("NeoDB rate limit reached (HTTP 429).")
        except Exception as e:
            logger.debug("NeoDB request failed for %s: %s", douban_id, e)
        return result

    def fetch_neodb_imdb_id(self, douban_id: str, timeout: int = 8) -> Optional[str]:
        """Fetch IMDb ID from NeoDB public catalog API."""
        return self.fetch_neodb_ids(douban_id, timeout=timeout).get("imdb_id")

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
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """Batch resolve TV episode/season IMDb IDs to parent Series metadata (IMDb, TMDb, TVDB) via Wikidata."""
        series_map: Dict[str, Dict[str, Optional[str]]] = {}
        valid_ids = list(set([str(x).strip() for x in episode_imdb_ids if str(x).strip().startswith("tt")]))
        if not valid_ids:
            return series_map

        for i in range(0, len(valid_ids), chunk_size):
            chunk = valid_ids[i : i + chunk_size]
            values_str = " ".join(f'"{did}"' for did in chunk)
            query = f"""
            SELECT ?ep_imdb ?series_imdb ?series_tmdb ?series_tvdb WHERE {{
              VALUES ?ep_imdb {{ {values_str} }}
              ?ep wdt:P345 ?ep_imdb .
              ?ep (wdt:P179|wdt:P361|wdt:P4969) ?series .
              OPTIONAL {{ ?series wdt:P345 ?series_imdb . }}
              OPTIONAL {{ ?series wdt:P4983 ?series_tmdb . }}
              OPTIONAL {{ ?series wdt:P4835 ?series_tvdb . }}
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
                        s_imdb = b.get("series_imdb", {}).get("value")
                        s_tmdb = b.get("series_tmdb", {}).get("value")
                        s_tvdb = b.get("series_tvdb", {}).get("value")
                        if ep and (s_imdb or s_tmdb or s_tvdb):
                            series_map[ep] = {
                                "series_imdb_id": s_imdb if (s_imdb and s_imdb.startswith("tt")) else None,
                                "tmdb_id": str(s_tmdb).strip() if s_tmdb else None,
                                "tvdb_id": str(s_tvdb).strip() if s_tvdb else None,
                            }
            except Exception as e:
                logger.warning("Wikidata parent series query failed: %s", e)

            if on_progress:
                try:
                    on_progress(min(i + chunk_size, len(valid_ids)), len(valid_ids), len(series_map))
                except Exception:
                    pass

            time.sleep(0.2)

        return series_map

    def resolve_series_metadata(
        self,
        episode_imdb_id: str,
        tmdb_api_key: Optional[str] = None,
        omdb_api_key: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        """Resolve parent series identifiers (IMDb, TMDb, TVDB) for a TV show episode."""
        result: Dict[str, Optional[str]] = {
            "series_imdb_id": None,
            "tmdb_id": None,
            "tvdb_id": None,
        }
        if not episode_imdb_id:
            return result

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
                    tv_results = data.get("tv_results", [])
                    show_id = None
                    if episodes and "show_id" in episodes[0]:
                        show_id = episodes[0]["show_id"]
                    elif tv_results and "id" in tv_results[0]:
                        show_id = tv_results[0]["id"]

                    if show_id:
                        result["tmdb_id"] = str(show_id)
                        show_url = f"https://api.themoviedb.org/3/tv/{show_id}/external_ids?api_key={tmdb_api_key}"
                        show_resp = self.session.get(show_url, timeout=10)
                        if show_resp.status_code == 200:
                            ext_data = show_resp.json()
                            if ext_data.get("imdb_id"):
                                result["series_imdb_id"] = ext_data["imdb_id"]
                            if ext_data.get("tvdb_id"):
                                result["tvdb_id"] = str(ext_data["tvdb_id"])
                        if result["series_imdb_id"] or result["tmdb_id"] or result["tvdb_id"]:
                            return result
            except Exception as e:
                logger.debug("TMDb series lookup error for %s: %s", episode_imdb_id, e)

        # 2. Try OMDb if API key is provided
        if omdb_api_key:
            try:
                omdb_url = f"http://www.omdbapi.com/?i={episode_imdb_id}&apikey={omdb_api_key}"
                resp = self.session.get(omdb_url, timeout=10)
                if resp.status_code == 200:
                    series_id = resp.json().get("seriesID")
                    if series_id and series_id.startswith("tt"):
                        result["series_imdb_id"] = series_id
                        return result
            except Exception as e:
                logger.debug("OMDb series lookup error for %s: %s", episode_imdb_id, e)

        # 3. Try Wikidata Knowledge Graph (free, no API key required)
        query = f"""
        SELECT ?series_imdb ?series_tmdb ?series_tvdb WHERE {{
          ?ep wdt:P345 "{episode_imdb_id}" .
          ?ep (wdt:P179|wdt:P361|wdt:P4969) ?series .
          OPTIONAL {{ ?series wdt:P345 ?series_imdb . }}
          OPTIONAL {{ ?series wdt:P4983 ?series_tmdb . }}
          OPTIONAL {{ ?series wdt:P4835 ?series_tvdb . }}
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
                    b = bindings[0]
                    s_id = b.get("series_imdb", {}).get("value")
                    s_tmdb = b.get("series_tmdb", {}).get("value")
                    s_tvdb = b.get("series_tvdb", {}).get("value")
                    if s_id and s_id.startswith("tt"):
                        result["series_imdb_id"] = s_id
                    if s_tmdb:
                        result["tmdb_id"] = str(s_tmdb).strip()
                    if s_tvdb:
                        result["tvdb_id"] = str(s_tvdb).strip()
        except Exception as e:
            logger.debug("Wikidata series lookup failed for %s: %s", episode_imdb_id, e)

        return result

    def resolve_series_imdb_id(
        self,
        episode_imdb_id: str,
        tmdb_api_key: Optional[str] = None,
        omdb_api_key: Optional[str] = None,
    ) -> Optional[str]:
        """Convert a TV episode/season IMDb ID to the parent Series IMDb ID."""
        meta = self.resolve_series_metadata(
            episode_imdb_id=episode_imdb_id,
            tmdb_api_key=tmdb_api_key,
            omdb_api_key=omdb_api_key,
        )
        return meta.get("series_imdb_id")

    def search_tmdb_title(
        self,
        title: str,
        year: Optional[Any] = None,
        is_tv: bool = False,
        tmdb_api_key: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        """Search TMDb by title and year to find IMDb, TMDb, and TVDB IDs."""
        result: Dict[str, Optional[str]] = {"imdb_id": None, "tmdb_id": None, "tvdb_id": None}
        if not tmdb_api_key or not title:
            return result

        clean_title = re.sub(r"第[一二两三四五六七八九十\d]+季", "", title).strip()
        endpoint = "tv" if is_tv else "movie"
        url = f"https://api.themoviedb.org/3/search/{endpoint}"
        params: Dict[str, Any] = {
            "api_key": tmdb_api_key,
            "query": clean_title or title,
            "language": "zh-CN",
        }
        if year:
            param_key = "first_air_date_year" if is_tv else "primary_release_year"
            params[param_key] = str(year)

        try:
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                matched_cand = None
                for cand in results:
                    if is_strict_tmdb_match(cand, clean_title or title, year):
                        matched_cand = cand
                        break

                if matched_cand and "id" in matched_cand:
                    media_id = matched_cand["id"]
                    result["tmdb_id"] = str(media_id)
                    ext_url = f"https://api.themoviedb.org/3/{endpoint}/{media_id}/external_ids?api_key={tmdb_api_key}"
                    ext_resp = self.session.get(ext_url, timeout=10)
                    if ext_resp.status_code == 200:
                        ext_data = ext_resp.json()
                        if ext_data.get("imdb_id"):
                            result["imdb_id"] = ext_data["imdb_id"]
                        if ext_data.get("tvdb_id"):
                            result["tvdb_id"] = str(ext_data["tvdb_id"])
                else:
                    logger.debug(
                        "TMDb search for '%s' (%s) rejected: no candidate strictly matched title & year.",
                        title,
                        year,
                    )
        except Exception as e:
            logger.debug("TMDb search failed for '%s': %s", title, e)

        return result

    def search_omdb_title(
        self,
        title: str,
        year: Optional[Any] = None,
        omdb_api_key: Optional[str] = None,
    ) -> Optional[str]:
        """Search OMDb by title and year to find IMDb ID with strict title/year validation."""
        if not omdb_api_key or not title:
            return None
        clean_title = re.sub(r"第[一二两三四五六七八九十\d]+季", "", title).strip()
        url = "http://www.omdbapi.com/"
        params: Dict[str, Any] = {"apikey": omdb_api_key, "t": clean_title or title}
        if year:
            params["y"] = str(year)
        try:
            resp = self.session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                omdb_title = data.get("Title", "")
                omdb_year = data.get("Year", "")
                norm_q = re.sub(r"[^\w\u4e00-\u9fa5]", "", (clean_title or title).lower())
                norm_o = re.sub(r"[^\w\u4e00-\u9fa5]", "", omdb_title.lower())
                if norm_q and norm_q == norm_o:
                    if year and omdb_year and omdb_year[:4].isdigit():
                        try:
                            if abs(int(omdb_year[:4]) - int(str(year)[:4])) > 1:
                                return None
                        except (ValueError, TypeError):
                            pass
                    imdb_id = data.get("imdbID")
                    if imdb_id and imdb_id.startswith("tt"):
                        return imdb_id
        except Exception as e:
            logger.debug("OMDb search failed for '%s': %s", title, e)
        return None

    def resolve_item(
        self,
        douban_id: str,
        title: str,
        is_tv: bool = False,
        year: Optional[Any] = None,
        tmdb_api_key: Optional[str] = None,
        omdb_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a Douban item, prioritizing Wikidata, NeoDB, TMDb, OMDb, and Douban."""
        douban_id = str(douban_id)
        season = extract_season_number(title)
        is_series = is_tv or (season is not None)

        # 1. Check local SQLite cache first (including Wikidata bulk pre-fetched items)
        if self.storage:
            cached = self.storage.get_imdb_mapping(douban_id)
            if cached and (cached.get("imdb_id") or cached.get("tmdb_id") or cached.get("tvdb_id")):
                imdb_id = cached.get("imdb_id")
                tmdb_id = cached.get("tmdb_id")
                tvdb_id = cached.get("tvdb_id")
                cached_season = cached.get("season") if cached.get("season") is not None else season
                series_imdb_id = cached.get("series_imdb_id")

                # If Season > 1 and parent series ID is missing, attempt series resolution
                if cached_season and cached_season > 1 and not series_imdb_id:
                    if imdb_id:
                        s_meta = self.resolve_series_metadata(
                            imdb_id, tmdb_api_key=tmdb_api_key, omdb_api_key=omdb_api_key
                        )
                        series_imdb_id = s_meta.get("series_imdb_id")
                        tmdb_id = tmdb_id or s_meta.get("tmdb_id")
                        tvdb_id = tvdb_id or s_meta.get("tvdb_id")

                        self.storage.save_imdb_mapping(
                            douban_id=douban_id,
                            imdb_id=imdb_id,
                            series_imdb_id=series_imdb_id,
                            season=cached_season,
                            title=title,
                            tmdb_id=tmdb_id,
                            tvdb_id=tvdb_id,
                        )
                return {
                    "douban_id": douban_id,
                    "imdb_id": imdb_id,
                    "series_imdb_id": series_imdb_id,
                    "tmdb_id": tmdb_id,
                    "tvdb_id": tvdb_id,
                    "season": cached_season,
                    "title": title or cached.get("title"),
                }

        imdb_id = None
        tmdb_id = None
        tvdb_id = None

        # 2. Query NeoDB public catalog
        try:
            imdb_id = self.fetch_neodb_imdb_id(douban_id)
            neodb_meta = self.fetch_neodb_ids(douban_id)
            if neodb_meta:
                imdb_id = imdb_id or neodb_meta.get("imdb_id")
                tmdb_id = neodb_meta.get("tmdb_id")
                tvdb_id = neodb_meta.get("tvdb_id")
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

        # 4. Fallback: TMDb search by title + year if API key provided and no ID found
        if tmdb_api_key and not (imdb_id or tmdb_id):
            try:
                tmdb_res = self.search_tmdb_title(title=title, year=year, is_tv=is_series, tmdb_api_key=tmdb_api_key)
                imdb_id = imdb_id or tmdb_res.get("imdb_id")
                tmdb_id = tmdb_id or tmdb_res.get("tmdb_id")
                tvdb_id = tvdb_id or tmdb_res.get("tvdb_id")
            except Exception as e:
                logger.debug("TMDb search fallback failed for %s: %s", title, e)

        # 5. Fallback: OMDb search by title + year if API key provided and no IMDb found
        if omdb_api_key and not imdb_id:
            try:
                imdb_id = self.search_omdb_title(title=title, year=year, omdb_api_key=omdb_api_key)
            except Exception as e:
                logger.debug("OMDb search fallback failed for %s: %s", title, e)

        # 6. Multi-season TV Series Parent Mapping
        series_imdb_id = None
        if (is_series or season is not None) and season and season > 1:
            if imdb_id:
                s_meta = self.resolve_series_metadata(
                    imdb_id, tmdb_api_key=tmdb_api_key, omdb_api_key=omdb_api_key
                )
                series_imdb_id = s_meta.get("series_imdb_id")
                tmdb_id = tmdb_id or s_meta.get("tmdb_id")
                tvdb_id = tvdb_id or s_meta.get("tvdb_id")

        result = {
            "douban_id": douban_id,
            "imdb_id": imdb_id,
            "series_imdb_id": series_imdb_id,
            "tmdb_id": tmdb_id,
            "tvdb_id": tvdb_id,
            "season": season,
            "title": title,
        }

        # Save to cache if any identifier was found
        if self.storage and (imdb_id or tmdb_id or tvdb_id):
            self.storage.save_imdb_mapping(
                douban_id=douban_id,
                imdb_id=imdb_id,
                series_imdb_id=series_imdb_id,
                season=season,
                title=title,
                tmdb_id=tmdb_id,
                tvdb_id=tvdb_id,
            )

        return result


DoubanResolver = ItemResolver
