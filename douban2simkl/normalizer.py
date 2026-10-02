"""Rating calibration engine and comment normalization for Simkl."""

import re
from typing import Optional, Tuple

CHINESE_HALF_STARS = {
    "四星半": 4.5,
    "三星半": 3.5,
    "两星半": 2.5,
    "二星半": 2.5,
    "一星半": 1.5,
}

UNIT_PATTERN = re.compile(r"^[0-9.]+\s*(?:小时|h|点|号|月|mm|cm|寸|岁|集)", re.I)
HALF_STAR_PATTERN = re.compile(r"(?:^|[^\d.])([1-4]\.5)(?:[^\d.]|$)")
TEN_POINT_PATTERN = re.compile(r"(?:^|[^\d.])(10|[1-9](?:\.[0-9])?)\s*(?:/10|分)(?:[^\d.]|$)")


def calibrate_rating(
    official_rating: Optional[int], comment: Optional[str]
) -> Tuple[Optional[int], str]:
    """Calibrate a movie/TV rating from official Douban stars (1-5) and comment text.

    Returns:
        tuple: (calibrated_score_1_to_10, calibration_reason)
    """
    if comment:
        # Check Chinese half-star phrases first
        for phrase, star_val in CHINESE_HALF_STARS.items():
            if phrase in comment:
                simkl_score = int(star_val * 2)
                return simkl_score, "comment_half_star"

        # Check decimal half-star numbers (1.5, 2.5, 3.5, 4.5)
        for match in HALF_STAR_PATTERN.finditer(comment):
            val_str = match.group(1)
            star_val = float(val_str)
            end_pos = match.end(1)
            sub_after = comment[end_pos : end_pos + 10]

            # Check if immediately followed by measurement units (e.g. 2.5小时)
            is_unit = bool(UNIT_PATTERN.match(val_str + sub_after))
            if is_unit:
                # If followed by unit and far from official rating, skip
                if official_rating is not None and abs(star_val - official_rating) > 1.0:
                    continue

            simkl_score = int(star_val * 2)
            return simkl_score, "comment_half_star"

        # Check explicit 10-point scale mentions (e.g. 8分, 7.5分, 9/10)
        ten_match = TEN_POINT_PATTERN.search(comment)
        if ten_match:
            val = float(ten_match.group(1))
            if 1 <= val <= 10:
                simkl_score = int(round(val))
                return simkl_score, "comment_ten_point"

    # Fallback to official rating
    if official_rating is not None and 1 <= official_rating <= 5:
        return official_rating * 2, "official"

    return None, "none"


def normalize_comment(comment: Optional[str]) -> Tuple[Optional[str], bool]:
    """Normalize and truncate comments to fit Simkl's 140-character memo limit.

    Returns:
        tuple: (truncated_text, is_truncated)
    """
    if not comment:
        return None, False

    clean_text = comment.strip()
    if not clean_text:
        return None, False

    if len(clean_text) <= 140:
        return clean_text, False

    truncated = clean_text[:137] + "..."
    return truncated, True


def clean_comment_tag(comment: str, season: int) -> str:
    """Strip any existing season tag from comment (e.g., [s01]:, [S1], S01:, etc.)."""
    if not comment:
        return ""
    cleaned = comment.strip()
    pattern = rf"^(?:\[?[sS]0?{season}\]?\s*[:：]?|第0?{season}季\s*[:：]?)\s*"
    cleaned = re.sub(pattern, "", cleaned).strip()
    return cleaned


def build_composite_show_memo(season_comments: dict, max_len: int = 140) -> str:
    """Build a composite multi-season memo for Simkl formatted as '[s01]: c1 ; [s02]: c2'.

    Simkl only supports a single 140-character memo for the entire TV show.
    This aggregates comments across all seasons and ensures total length <= max_len.
    """
    if not season_comments:
        return ""

    cleaned_map: dict = {}
    for s_num, text in season_comments.items():
        if text:
            cleaned = clean_comment_tag(str(text), int(s_num))
            if cleaned:
                cleaned_map[int(s_num)] = cleaned

    if not cleaned_map:
        return ""

    sorted_seasons = sorted(cleaned_map.keys())

    # Single season case
    if len(sorted_seasons) == 1:
        s = sorted_seasons[0]
        tag = f"[s{s:02d}]: "
        avail = max_len - len(tag)
        c = cleaned_map[s]
        return f"{tag}{c[:avail]}" if len(c) > avail else f"{tag}{c}"

    # Multiple seasons: check if full text fits
    full_candidate = " ; ".join(f"[s{s:02d}]: {cleaned_map[s]}" for s in sorted_seasons)
    if len(full_candidate) <= max_len:
        return full_candidate

    sep = " ; "
    included_seasons = list(sorted_seasons)

    # Ensure we don't have more seasons than can realistically fit with tags and minimal text
    while len(included_seasons) > 1 and (
        len(sep) * (len(included_seasons) - 1)
        + sum(len(f"[s{s:02d}]: ") + 4 for s in included_seasons)
    ) > max_len:
        included_seasons.pop(0)

    tags_total_len = sum(len(f"[s{s:02d}]: ") for s in included_seasons) + len(sep) * (
        len(included_seasons) - 1
    )
    avail_text = max_len - tags_total_len

    # Allocate text budget from latest season to oldest
    allocated: dict = {}
    reversed_seasons = list(reversed(included_seasons))
    rem_avail = avail_text

    for i, s in enumerate(reversed_seasons):
        c = cleaned_map[s]
        num_others_left = len(reversed_seasons) - 1 - i
        reserved_for_others = min(num_others_left * 4, rem_avail)
        max_for_this = max(0, rem_avail - reserved_for_others)
        if len(c) <= max_for_this:
            allocated[s] = c
            rem_avail -= len(c)
        else:
            allocated[s] = c[:max_for_this].rstrip()
            rem_avail -= len(allocated[s])

    parts = [f"[s{s:02d}]: {allocated[s]}" for s in included_seasons if allocated.get(s)]
    res = sep.join(parts)
    return res[:max_len]

