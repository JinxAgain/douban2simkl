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
