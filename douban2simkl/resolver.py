"""Douban ID to IMDb ID and TV multi-season Series ID resolver."""

import re
import requests
from typing import Optional, Dict, Any
from douban2simkl.storage import Storage

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

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
    ),
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

    def fetch_douban_imdb_id(self, douban_id: str) -> Optional[str]:
        """Fetch IMDb ID directly from Douban pages."""
        # 1. Try mobile description page
        try:
            url = DOUBAN_DESC_URL.format(douban_id)
            resp = self.session.get(url, headers=COMMON_HEADERS, timeout=12)
            if resp.status_code == 200:
                match = IMDB_DESC_PATTERN.search(resp.text)
                if match:
                    return match.group(1)
        except Exception:
            pass

        # 2. Fallback to desktop subject page
        try:
            url = DOUBAN_DESKTOP_URL.format(douban_id)
            resp = self.session.get(url, headers=COMMON_HEADERS, timeout=12)
            if resp.status_code == 200:
                match = IMDB_DESKTOP_PATTERN.search(resp.text)
                if match:
                    return match.group(1)
        except Exception:
            pass

        return None

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

        return None

    def resolve_item(
        self,
        douban_id: str,
        title: str,
        is_tv: bool = False,
        tmdb_api_key: Optional[str] = None,
        omdb_api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a Douban item, utilizing local SQLite cache when available."""
        # Check cache
        if self.storage:
            cached = self.storage.get_imdb_mapping(douban_id)
            if cached and cached.get("imdb_id"):
                return cached

        season = extract_season_number(title)
        imdb_id = self.fetch_douban_imdb_id(douban_id)
        series_imdb_id = None

        if imdb_id and (is_tv or season is not None):
            # If Season > 1 or TV show, resolve parent series IMDb ID
            if season and season > 1:
                series_imdb_id = self.resolve_series_imdb_id(
                    imdb_id, tmdb_api_key=tmdb_api_key, omdb_api_key=omdb_api_key
                )

        result = {
            "douban_id": str(douban_id),
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
