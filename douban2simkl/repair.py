"""Repair Simkl history damaged by a previous sync run.

A buggy run posted show payloads without "seasons", which Simkl treats as "mark every
episode watched now". This tool:
  1. Finds every episode whose watched_at falls inside the given time window(s)
     (the exact minutes the faulty run executed, so genuine watches are untouched).
  2. Removes those episodes from Simkl history.
  3. Re-adds only the seasons the user actually marked as watched on Douban,
     stamped with the Douban timestamp.

Dry-run by default; nothing is written unless apply=True.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from douban2simkl.normalizer import calibrate_rating, to_simkl_iso
from douban2simkl.simkl import SIMKL_API_BASE, SimklClient
from douban2simkl.tv_grouping import build_tv_season_plan

logger = logging.getLogger(__name__)

Window = Tuple[datetime, datetime]


def parse_window(text: str) -> Window:
    """Parse 'START/END' (ISO-8601, local time if no offset) into an aware datetime range."""
    local_tz = datetime.now().astimezone().tzinfo
    start_s, end_s = text.split("/", 1)
    start = datetime.fromisoformat(start_s.strip())
    end = datetime.fromisoformat(end_s.strip())
    if start.tzinfo is None:
        start = start.replace(tzinfo=local_tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=local_tz)
    if end <= start:
        raise ValueError(f"Window end must be after start: {text}")
    return start, end


def _in_windows(watched_at: Optional[str], windows: List[Window]) -> bool:
    if not watched_at:
        return False
    t = datetime.fromisoformat(watched_at.replace("Z", "+00:00"))
    return any(s <= t < e for s, e in windows)


def fetch_simkl_episode_history(client: SimklClient) -> List[Dict[str, Any]]:
    """Fetch shows and anime with per-episode watched_at."""
    items: List[Dict[str, Any]] = []
    for kind in ("shows", "anime"):
        resp = client.session.get(
            f"{SIMKL_API_BASE}/sync/all-items/{kind}",
            params={"extended": "full", "episode_watched_at": "yes", "include_all_episodes": "yes"},
            timeout=60,
        )
        resp.raise_for_status()
        for it in (resp.json() or {}).get(kind, []) or []:
            it["_kind"] = kind
            items.append(it)
    return items


def _douban_index(records: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]:
    """Index Douban 'done' TV records by every identifier they carry."""
    pairs = [(r, r) for r in records]
    plan = build_tv_season_plan(pairs)
    by_token: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        did = str(r.get("douban_id", ""))
        p = plan.get(did)
        if not p or p["ambiguous"] or r.get("status", "done") != "done":
            continue
        entry = {"record": r, "season": p["season"], "own_imdb": r.get("imdb_id")}
        tokens = {str(v).lower() for v in p["ids"].values() if v}
        for k in ("imdb_id", "series_imdb_id", "tmdb_id", "tvdb_id"):
            if r.get(k):
                tokens.add(str(r[k]).lower())
        for t in tokens:
            by_token[t].append(entry)
    return by_token, plan


def build_repair_plan(
    simkl_items: List[Dict[str, Any]],
    douban_records: List[Dict[str, Any]],
    windows: List[Window],
    manual_map: Optional[Dict[int, str]] = None,
) -> List[Dict[str, Any]]:
    """Compute per-show repair actions without touching Simkl.

    manual_map: optional {simkl_id: douban_id} overrides for entries that cannot be matched
    by ids (e.g. anime sequels that Simkl stores as separate single-season entries).
    """
    by_token, _plan = _douban_index(douban_records)
    records_by_id = {str(r.get("douban_id", "")): r for r in douban_records}
    manual_map = manual_map or {}
    actions: List[Dict[str, Any]] = []

    for it in simkl_items:
        show = it.get("show") or it.get("anime") or {}
        ids = show.get("ids", {}) or {}
        damaged: Dict[int, List[int]] = defaultdict(list)
        for s in it.get("seasons", []) or []:
            for e in s.get("episodes", []) or []:
                if _in_windows(e.get("watched_at"), windows):
                    damaged[int(s.get("number"))].append(int(e.get("number")))
        if not damaged:
            continue

        simkl_id = ids.get("simkl")
        override = records_by_id.get(str(manual_map.get(int(simkl_id)))) if simkl_id and int(simkl_id) in manual_map else None

        # Douban seasons belonging to this Simkl entry
        candidates: Dict[str, Dict[str, Any]] = {}
        for v in ids.values():
            for entry in by_token.get(str(v).lower(), []):
                candidates[str(entry["record"]["douban_id"])] = entry

        all_seasons = {int(s.get("number")) for s in it.get("seasons", []) or []}
        readd: List[Dict[str, Any]] = []
        remove_only: List[int] = []
        for season_no in sorted(damaged):
            rec: Optional[Dict[str, Any]] = None
            if override:
                rec = override
            else:
                match = [c for c in candidates.values() if c["season"] == season_no]
                if not match and it["_kind"] == "anime" and all_seasons == {1} and candidates:
                    # Simkl splits anime seasons into separate entries numbered S1; match by the
                    # record's own (season-level) imdb id instead of its Douban season number.
                    id_values = {str(x).lower() for x in ids.values()}
                    own = [c for c in candidates.values() if c["own_imdb"] and str(c["own_imdb"]).lower() in id_values]
                    match = own if len(own) == 1 else []
                if match:
                    rec = sorted(match, key=lambda c: c["record"].get("create_time") or "")[0]["record"]
            if rec:
                readd.append({
                    "season": season_no,
                    "watched_at": to_simkl_iso(rec.get("create_time")),
                    "douban_title": rec.get("title"),
                    "rating": calibrate_rating(rec.get("official_rating") or rec.get("rating"),
                                               rec.get("comment") or "")[0],
                })
            else:
                remove_only.append(season_no)

        matched = bool(candidates) or bool(override)
        actions.append({
            "title": show.get("title"),
            "simkl_id": simkl_id,
            "kind": it["_kind"],
            "matched": matched,
            # Unmatched entries are reported but never modified: we cannot tell which seasons are legit
            "skip": not matched,
            "damaged": {k: sorted(v) for k, v in damaged.items()},
            "readd": readd,
            "remove_only": remove_only if matched else [],
        })
    return actions


def apply_repair(client: SimklClient, actions: List[Dict[str, Any]]) -> Dict[str, int]:
    """Remove damaged episodes, then re-add Douban seasons with their Douban timestamps."""
    stats = {"removed_episodes": 0, "readded_seasons": 0}
    for a in actions:
        if not a["simkl_id"] or a.get("skip"):
            continue
        show_ids = {"simkl": a["simkl_id"]}
        remove_payload = {
            "shows": [{
                "ids": show_ids,
                "seasons": [
                    {"number": s, "episodes": [{"number": e} for e in eps]}
                    for s, eps in sorted(a["damaged"].items())
                ],
            }]
        }
        resp = client._post_with_rate_limit(f"{SIMKL_API_BASE}/sync/history/remove", remove_payload)
        deleted = (resp.json() or {}).get("deleted", {}) or {}
        stats["removed_episodes"] += int(deleted.get("episodes", 0) or 0)

        for r in a["readd"]:
            season_obj: Dict[str, Any] = {"number": r["season"]}
            show_obj: Dict[str, Any] = {"ids": show_ids, "seasons": [season_obj]}
            if r["watched_at"]:
                show_obj["watched_at"] = r["watched_at"]
                season_obj["watched_at"] = r["watched_at"]
            if r.get("rating"):
                show_obj["rating"] = r["rating"]
            client.sync_history_batch(shows=[show_obj])
            stats["readded_seasons"] += 1
    return stats


def format_repair_report(actions: List[Dict[str, Any]]) -> str:
    todo = [a for a in actions if not a.get("skip")]
    skipped = [a for a in actions if a.get("skip")]
    lines = ["# Simkl repair plan", ""]
    total_eps = sum(len(v) for a in todo for v in a["damaged"].values())
    lines.append(f"Shows to repair: {len(todo)} | episodes to remove: {total_eps} | skipped (no Douban match): {len(skipped)}")
    lines.append("")
    lines.append("| Show | Simkl ID | Remove (season: episodes) | Re-add from Douban (season @ UTC time) | Not on Douban (removed only) |")
    lines.append("|---|---|---|---|---|")
    for a in sorted(todo, key=lambda x: str(x["title"])):
        dmg = ", ".join(f"S{s}: {len(e)} eps" for s, e in sorted(a["damaged"].items()))
        rd = ", ".join(f"S{r['season']} @ {r['watched_at']}" for r in a["readd"]) or "-"
        ro = ", ".join(f"S{s}" for s in a["remove_only"]) or "-"
        lines.append(f"| {a['title']} | {a['simkl_id']} | {dmg} | {rd} | {ro} |")
    if skipped:
        lines.append("")
        lines.append("## Skipped — not matched to any Douban record (left untouched)")
        lines.append("")
        lines.append("| Show | Simkl ID | Type | Episodes stamped in window |")
        lines.append("|---|---|---|---|")
        for a in sorted(skipped, key=lambda x: str(x["title"])):
            dmg = ", ".join(f"S{s}: {len(e)} eps" for s, e in sorted(a["damaged"].items()))
            lines.append(f"| {a['title']} | {a['simkl_id']} | {a['kind']} | {dmg} |")
    return "\n".join(lines) + "\n"


def load_backup(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def find_oct2_faulty_shows(
    simkl_items: List[Dict[str, Any]],
    preserve_simkl_ids: Optional[Set[int]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Identify shows touched during the faulty October 2, 2026 run.

    Preserves user genuine watches such as 'The Thick of It' (Simkl ID 9538) and '35 Up'.
    """
    if preserve_simkl_ids is None:
        preserve_simkl_ids = {9538}

    delete_list: List[Dict[str, Any]] = []
    preserved_list: List[Dict[str, Any]] = []

    for it in simkl_items:
        kind = it.get("_kind") or ("anime" if "anime" in it else "shows")
        show_obj = it.get("show") or it.get("anime") or {}
        title = show_obj.get("title", "")
        ids = show_obj.get("ids", {}) or {}
        simkl_id = ids.get("simkl")

        last_watched = it.get("last_watched_at", "")
        has_oct2 = "2026-10-02" in str(last_watched)
        if not has_oct2:
            for s in it.get("seasons", []) or []:
                for ep in s.get("episodes", []) or []:
                    if "2026-10-02" in str(ep.get("watched_at", "")):
                        has_oct2 = True
                        break

        if not has_oct2:
            continue

        item_info = {
            "kind": kind,
            "title": title,
            "simkl_id": simkl_id,
            "ids": ids,
            "last_watched": last_watched,
        }

        # Check preservation rule
        is_preserved = (
            (simkl_id is not None and int(simkl_id) in preserve_simkl_ids)
            or ("thick of it" in title.lower())
            or ("35 up" in title.lower())
        )

        if is_preserved:
            preserved_list.append(item_info)
        else:
            delete_list.append(item_info)

    return delete_list, preserved_list


def delete_shows_from_simkl(
    client: SimklClient,
    shows_to_delete: List[Dict[str, Any]],
    batch_size: int = 50,
) -> Dict[str, int]:
    """Delete faulty shows completely from Simkl library and clear ratings."""
    stats = {"deleted_shows": 0, "deleted_ratings": 0}
    simkl_ids = [s["simkl_id"] for s in shows_to_delete if s.get("simkl_id")]

    for i in range(0, len(simkl_ids), batch_size):
        batch = simkl_ids[i : i + batch_size]
        payload = {"shows": [{"ids": {"simkl": sid}} for sid in batch]}

        # 1. Remove from history / library
        try:
            resp_hist = client._post_with_rate_limit(
                f"{SIMKL_API_BASE}/sync/history/remove", payload
            )
            data_hist = resp_hist.json() or {}
            del_count = data_hist.get("deleted", {}).get("shows", len(batch))
            stats["deleted_shows"] += int(del_count or len(batch))
        except Exception as e:
            logger.error("Failed to remove shows batch from Simkl history: %s", e)

        # 2. Clear ratings if any
        try:
            resp_rate = client._post_with_rate_limit(
                f"{SIMKL_API_BASE}/sync/ratings/remove", payload
            )
            data_rate = resp_rate.json() or {}
            del_rate = data_rate.get("deleted", {}).get("shows", 0)
            stats["deleted_ratings"] += int(del_rate or 0)
        except Exception as e:
            logger.debug("Failed to remove show ratings: %s", e)

    return stats


def reset_local_sync_state(db_path: str, deleted_items: List[Dict[str, Any]]) -> int:
    """Reset sync_state in local SQLite DB for Douban items belonging to deleted shows."""
    import sqlite3

    target_tokens: Set[str] = set()
    for item in deleted_items:
        for v in (item.get("ids") or {}).values():
            if v:
                target_tokens.add(str(v).lower().strip())

    if not target_tokens:
        return 0

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT douban_id, imdb_id, series_imdb_id, tmdb_id, tvdb_id FROM imdb_cache")
        matching_douban_ids: List[str] = []
        for douban_id, imdb_id, series_imdb_id, tmdb_id, tvdb_id in c.fetchall():
            row_tokens = {
                str(x).lower().strip()
                for x in (imdb_id, series_imdb_id, tmdb_id, tvdb_id)
                if x
            }
            if row_tokens.intersection(target_tokens):
                matching_douban_ids.append(str(douban_id))

        if matching_douban_ids:
            placeholders = ",".join("?" for _ in matching_douban_ids)
            c.execute(
                f"DELETE FROM sync_state WHERE douban_id IN ({placeholders})",
                matching_douban_ids,
            )
            conn.commit()
            return len(matching_douban_ids)
        return 0
    finally:
        conn.close()


def format_oct2_cleanup_report(
    delete_list: List[Dict[str, Any]],
    preserved_list: List[Dict[str, Any]],
) -> str:
    """Format markdown report of shows to delete vs preserve."""
    lines = [
        "# October 2, 2026 Faulty Shows Cleanup Plan",
        "",
        f"**Shows to completely remove from Simkl:** {len(delete_list)}",
        f"**Shows explicitly preserved:** {len(preserved_list)}",
        "",
        "| # | Type | Simkl ID | Show Title | Last Watched Timestamp | Action |",
        "|---|---|---|---|---|---|",
    ]
    idx = 1
    for s in sorted(preserved_list, key=lambda x: str(x["title"])):
        lines.append(
            f"| {idx} | {s['kind']} | {s['simkl_id']} | **{s['title']}** | {s['last_watched']} | **PRESERVE** |"
        )
        idx += 1
    for s in sorted(delete_list, key=lambda x: str(x["title"])):
        lines.append(
            f"| {idx} | {s['kind']} | {s['simkl_id']} | {s['title']} | {s['last_watched']} | DELETE |"
        )
        idx += 1
    return "\n".join(lines) + "\n"

