"""Group Douban TV records into parent Simkl shows and assign a reliable season number to each.

Douban stores every season as a separate subject, and titles are inconsistent:
  - "行尸走肉 第八季"      -> explicit season marker
  - "俗女养成记2"          -> trailing digit, no marker
  - "鬼灭之刃：游郭篇"      -> arc name, no season number at all

Pushing a record with the wrong season (or without any season) to Simkl marks the wrong
episodes as watched, so this module only assigns a season when it can be justified, and
flags everything else as ambiguous so it is skipped instead of guessed.
"""

import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from douban2simkl.resolver import extract_season_number

# "俗女养成记2", "爱情公寓 5" (1-2 digit suffix; excludes years such as "请回答1988")
TRAILING_DIGIT_PATTERN = re.compile(r"^(.*?\D)\s*(\d{1,2})$")
SEASON_MARKER_PATTERN = re.compile(r"\s*第[一二两三四五六七八九十\d]+季.*$")
MAX_INFERRED_SEASON = 30


def _norm(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fa5]", "", (text or "").lower())


def _base_title(title: str) -> str:
    return SEASON_MARKER_PATTERN.sub("", title or "").strip()


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_tv_season_plan(
    records: List[Tuple[Dict[str, Any], Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """Build a per-record plan for TV items.

    Args:
        records: list of (douban_item, resolved_ids) pairs (movies are ignored).

    Returns:
        Mapping douban_id -> {
            "group": str,             # parent show group key (shared by all seasons of a show)
            "season": Optional[int],  # season number to push, or None when ambiguous
            "ambiguous": bool,        # True if the season cannot be determined safely
            "ids": Dict[str, str],    # canonical parent show ids for Simkl
        }
    """
    tv_entries: List[Dict[str, Any]] = []
    for item, resolved in records:
        title = item.get("title", "") or ""
        explicit_season = resolved.get("season") or extract_season_number(title)
        if item.get("type", "movie") != "tv" and not explicit_season:
            continue
        tv_entries.append({
            "douban_id": str(item.get("douban_id", "")),
            "title": title,
            "explicit_season": explicit_season,
            "imdb_id": resolved.get("imdb_id"),
            "series_imdb_id": resolved.get("series_imdb_id"),
            "tmdb_id": str(resolved["tmdb_id"]) if resolved.get("tmdb_id") else None,
            "tvdb_id": str(resolved["tvdb_id"]) if resolved.get("tvdb_id") else None,
        })

    # 1. Group seasons of the same show via shared identifiers (union-find over id tokens).
    #    A record's own imdb_id only identifies the show when it is not a season-level entry.
    uf = _UnionFind()
    for e in tv_entries:
        tokens = [f"rec:{e['douban_id']}"]
        if e["tmdb_id"]:
            tokens.append(f"tmdb:{e['tmdb_id']}")
        if e["tvdb_id"]:
            tokens.append(f"tvdb:{e['tvdb_id']}")
        if e["series_imdb_id"]:
            tokens.append(f"imdb:{e['series_imdb_id']}")
        elif e["imdb_id"] and (e["explicit_season"] in (None, 1)):
            tokens.append(f"imdb:{e['imdb_id']}")
        for t in tokens[1:]:
            uf.union(tokens[0], t)

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in tv_entries:
        groups[uf.find(f"rec:{e['douban_id']}")].append(e)

    plan: Dict[str, Dict[str, Any]] = {}
    for gkey, members in groups.items():
        # 2. Assign seasons inside the group
        claimed = {m["explicit_season"] for m in members if m["explicit_season"]}
        member_bases = {_norm(_base_title(m["title"])) for m in members}
        min_base_len = min(len(_norm(_base_title(m["title"]))) for m in members)

        for m in members:
            season = m["explicit_season"]
            ambiguous = False
            if not season:
                trailing = TRAILING_DIGIT_PATTERN.match(m["title"].strip())
                inferred: Optional[int] = None
                if trailing and len(members) > 1:
                    num = int(trailing.group(2))
                    stem = _norm(trailing.group(1))
                    other_bases = {_norm(_base_title(o["title"])) for o in members if o is not m}
                    for o in members:
                        om = TRAILING_DIGIT_PATTERN.match(o["title"].strip()) if o is not m else None
                        if om:
                            other_bases.add(_norm(om.group(1)))
                    if 2 <= num <= MAX_INFERRED_SEASON and stem in other_bases:
                        inferred = num
                if inferred:
                    season = inferred
                elif len(members) == 1:
                    season = 1
                elif len(_norm(_base_title(m["title"]))) == min_base_len and 1 not in claimed:
                    # The plain title (e.g. "鬼灭之刃" next to "鬼灭之刃：游郭篇") is the first season
                    season = 1
                elif len(_norm(_base_title(m["title"]))) == min_base_len and len(member_bases) == 1:
                    season = 1
                else:
                    ambiguous = True
            m["season"] = season
            m["ambiguous"] = ambiguous

        # 3. Canonical show ids: parent series imdb > season-1 imdb; plus tmdb / tvdb
        ids: Dict[str, str] = {}
        series_imdb = next((m["series_imdb_id"] for m in members if m["series_imdb_id"]), None)
        if not series_imdb:
            series_imdb = next(
                (m["imdb_id"] for m in members if m["imdb_id"] and m.get("season") == 1 and not m["ambiguous"]),
                None,
            )
        if series_imdb:
            ids["imdb"] = series_imdb
        tmdb = next((m["tmdb_id"] for m in members if m["tmdb_id"]), None)
        if tmdb:
            ids["tmdb"] = tmdb
        tvdb = next((m["tvdb_id"] for m in members if m["tvdb_id"]), None)
        if tvdb:
            ids["tvdb"] = tvdb

        for m in members:
            plan[m["douban_id"]] = {
                "group": gkey,
                "season": m["season"],
                "ambiguous": m["ambiguous"],
                "ids": dict(ids),
            }

    return plan
