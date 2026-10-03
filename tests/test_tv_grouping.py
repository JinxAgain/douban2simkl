from douban2simkl.normalizer import to_simkl_iso
from douban2simkl.tv_grouping import build_tv_season_plan


def _rec(did, title, imdb=None, series=None, tmdb=None, season=None, typ="tv"):
    return (
        {"douban_id": did, "title": title, "type": typ},
        {"imdb_id": imdb, "series_imdb_id": series, "tmdb_id": tmdb, "season": season},
    )


def test_to_simkl_iso_converts_beijing_time_to_utc():
    assert to_simkl_iso("2017-11-29 00:08:15") == "2017-11-28T16:08:15Z"
    assert to_simkl_iso("2021-10-17") == "2021-10-16T16:00:00Z"
    assert to_simkl_iso("2026-05-15T22:30:00Z") == "2026-05-15T22:30:00Z"
    assert to_simkl_iso("") is None
    assert to_simkl_iso(None) is None
    assert to_simkl_iso("not a date") is None


def test_plan_groups_walking_dead_seasons_into_one_show():
    plan = build_tv_season_plan([
        _rec("1", "行尸走肉 第一季", imdb="tt1520211", tmdb="1402", season=1),
        _rec("2", "行尸走肉 第二季", imdb="tt1790548", series="tt1520211", tmdb="1402", season=2),
        _rec("8", "行尸走肉 第八季", imdb="tt6156390", series="tt1520211", tmdb="1402", season=8),
    ])
    assert len({p["group"] for p in plan.values()}) == 1
    assert [plan[k]["season"] for k in ("1", "2", "8")] == [1, 2, 8]
    assert plan["8"]["ids"] == {"imdb": "tt1520211", "tmdb": "1402"}


def test_plan_infers_trailing_digit_seasons_only_with_siblings():
    plan = build_tv_season_plan([
        _rec("a", "爱情公寓", imdb="tt1862521", tmdb="68809"),
        _rec("b", "爱情公寓4", imdb="tt5492756", tmdb="68809"),
        _rec("c", "爱情公寓5", imdb="tt11592198", tmdb="68809"),
    ])
    assert [plan[k]["season"] for k in "abc"] == [1, 4, 5]
    # Canonical show imdb is the season-1 entry, never a later season's own imdb
    assert plan["c"]["ids"]["imdb"] == "tt1862521"


def test_plan_trailing_digit_without_plain_first_season():
    plan = build_tv_season_plan([
        _rec("b", "爱情公寓4", imdb="tt5492756", tmdb="68809"),
        _rec("c", "爱情公寓5", imdb="tt11592198", tmdb="68809"),
    ])
    assert plan["b"]["season"] == 4
    assert plan["c"]["season"] == 5


def test_plan_standalone_title_with_digit_is_not_a_season():
    # "请回答1988" is a title, and a lone record must stay season 1
    plan = build_tv_season_plan([_rec("x", "请回答1988", imdb="tt5182866", tmdb="64010")])
    assert plan["x"]["season"] == 1
    assert plan["x"]["ambiguous"] is False


def test_plan_flags_arc_titles_as_ambiguous():
    plan = build_tv_season_plan([
        _rec("1", "鬼灭之刃", imdb="tt9335498", tmdb="85937"),
        _rec("2", "鬼灭之刃：游郭篇", imdb="tt15757634", tmdb="85937"),
        _rec("3", "毛骗 第一季", imdb="tt20119656", tmdb="78013", season=1),
        _rec("4", "毛骗 终结篇", tmdb="78013"),
    ])
    assert plan["1"]["season"] == 1 and not plan["1"]["ambiguous"]
    assert plan["2"]["ambiguous"] is True
    assert plan["3"]["season"] == 1
    assert plan["4"]["ambiguous"] is True


def test_plan_ignores_movies():
    plan = build_tv_season_plan([_rec("m", "惊天魔盗团2", imdb="tt3110958", typ="movie")])
    assert plan == {}
