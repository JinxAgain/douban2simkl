import pytest
from douban2simkl.normalizer import calibrate_rating, normalize_comment


def test_calibrate_rating_official_fallback():
    score, reason = calibrate_rating(4, "一部非常棒的电影，剧情紧凑。")
    assert score == 8
    assert reason == "official"


def test_calibrate_rating_half_star_numeric():
    # 3.5 in comment overrides official 4 or 3
    score, reason = calibrate_rating(4, "3.5吧，前段还行，结尾略显仓促。")
    assert score == 7
    assert reason == "comment_half_star"

    score, reason = calibrate_rating(3, "给3.5星，值得一看。")
    assert score == 7
    assert reason == "comment_half_star"

    score, reason = calibrate_rating(4, "4.5分！年度最佳！")
    assert score == 9
    assert reason == "comment_half_star"

    score, reason = calibrate_rating(2, "2.5，勉强及格。")
    assert score == 5
    assert reason == "comment_half_star"

    score, reason = calibrate_rating(1, "1.5星烂片")
    assert score == 3
    assert reason == "comment_half_star"


def test_calibrate_rating_half_star_chinese():
    score, reason = calibrate_rating(4, "四星半，非常精彩！")
    assert score == 9
    assert reason == "comment_half_star"

    score, reason = calibrate_rating(3, "三星半吧。")
    assert score == 7
    assert reason == "comment_half_star"

    score, reason = calibrate_rating(2, "两星半，剧情拖沓。")
    assert score == 5
    assert reason == "comment_half_star"


def test_calibrate_rating_when_official_is_none():
    score, reason = calibrate_rating(None, "3.5分")
    assert score == 7
    assert reason == "comment_half_star"

    score, reason = calibrate_rating(None, "没有任何评分数字的短评")
    assert score is None
    assert reason == "none"


def test_calibrate_rating_avoid_unit_false_positives():
    # When unit is present and official rating is far, do not override
    score, reason = calibrate_rating(5, "前2.5小时很平淡，最后半小时神作！")
    assert score == 10
    assert reason == "official"


def test_normalize_comment_length():
    # None comment
    text, truncated = normalize_comment(None)
    assert text is None
    assert truncated is False

    # Short comment <= 140
    short_text = "这是一条很短的评价，非常精彩！"
    text, truncated = normalize_comment(short_text)
    assert text == short_text
    assert truncated is False

    # Exactly 140 chars
    exact_text = "A" * 140
    text, truncated = normalize_comment(exact_text)
    assert text == exact_text
    assert truncated is False

    # Over 140 chars
    long_text = "A" * 150
    text, truncated = normalize_comment(long_text)
    assert len(text) == 140
    assert text.endswith("...")
    assert truncated is True


def test_clean_comment_tag():
    from douban2simkl.normalizer import clean_comment_tag

    assert clean_comment_tag("[s01]: 剧情紧凑", 1) == "剧情紧凑"
    assert clean_comment_tag("[S1] 精彩", 1) == "精彩"
    assert clean_comment_tag("s02: 渐入佳境", 2) == "渐入佳境"
    assert clean_comment_tag("第3季：神作", 3) == "神作"
    assert clean_comment_tag("没有任何前缀的评论", 1) == "没有任何前缀的评论"
    assert clean_comment_tag("", 1) == ""


def test_build_composite_show_memo():
    from douban2simkl.normalizer import build_composite_show_memo

    # Empty dict
    assert build_composite_show_memo({}) == ""
    assert build_composite_show_memo({1: ""}) == ""

    # Single season
    single = build_composite_show_memo({1: "好看"})
    assert single == "[s01]: 好看"

    # Single season with existing tag stripped
    single_tag = build_composite_show_memo({2: "[s02]: 精彩"})
    assert single_tag == "[s02]: 精彩"

    # Multiple seasons fitting in 140 chars
    multi = build_composite_show_memo({
        1: "节奏真快",
        2: "复刻社交网络",
        4: "封神之作",
    })
    assert multi == "[s01]: 节奏真快 ; [s02]: 复刻社交网络 ; [s04]: 封神之作"

    # Long comments budget and truncation
    long_multi = build_composite_show_memo({
        1: "A" * 80,
        2: "B" * 80,
    })
    assert len(long_multi) <= 140
    assert "[s01]: " in long_multi
    assert "[s02]: " in long_multi
    assert " ; " in long_multi

