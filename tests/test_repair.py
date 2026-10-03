from douban2simkl.repair import build_repair_plan, parse_window


def _simkl_show(simkl_id, title, ids, seasons, kind="shows"):
    return {
        "_kind": kind,
        "show": {"title": title, "ids": dict(ids, simkl=simkl_id)},
        "seasons": [
            {"number": s, "episodes": [{"number": e, "watched_at": wa} for e, wa in eps]}
            for s, eps in seasons.items()
        ],
    }


BAD = "2026-10-02T17:14:30Z"   # 19:14 local (UTC+2) -> inside window
GOOD = "2017-04-06T11:48:45Z"  # original Douban-era watch -> outside window
WINDOW = [parse_window("2026-10-02T19:14:00+02:00/2026-10-02T19:15:00+02:00")]

DOUBAN = [
    {"douban_id": "1", "title": "行尸走肉 第一季", "type": "tv", "status": "done",
     "create_time": "2017-04-06 19:48:45", "imdb_id": "tt1520211", "tmdb_id": "1402", "season": 1},
    {"douban_id": "8", "title": "行尸走肉 第八季", "type": "tv", "status": "done",
     "create_time": "2017-11-29 00:08:15", "imdb_id": "tt6156390", "series_imdb_id": "tt1520211",
     "tmdb_id": "1402", "season": 8},
]


def test_repair_removes_seasons_not_on_douban_and_readds_douban_seasons_with_douban_time():
    twd = _simkl_show(2090, "The Walking Dead", {"imdb": "tt1520211", "tmdb": "1402"}, {
        1: [(1, GOOD), (2, GOOD)],
        8: [(1, BAD), (2, BAD)],
        9: [(1, BAD), (2, BAD)],
    })
    actions = build_repair_plan([twd], DOUBAN, WINDOW)
    assert len(actions) == 1
    a = actions[0]
    assert a["skip"] is False
    # Season 1 was stamped outside the window -> untouched
    assert a["damaged"] == {8: [1, 2], 9: [1, 2]}
    assert [r["season"] for r in a["readd"]] == [8]
    assert a["readd"][0]["watched_at"] == "2017-11-28T16:08:15Z"
    assert a["remove_only"] == [9]


def test_repair_never_touches_unmatched_entries():
    # Anime sequel stored by Simkl as a separate entry with no ids shared with Douban
    sequel = _simkl_show(2665178, "Dan Da Dan", {"mal": "60543"}, {1: [(1, BAD), (2, BAD)]}, kind="anime")
    actions = build_repair_plan([sequel], DOUBAN, WINDOW)
    assert actions[0]["skip"] is True
    assert actions[0]["remove_only"] == []


def test_repair_manual_map_readds_with_mapped_douban_time():
    douban = DOUBAN + [{"douban_id": "dd2", "title": "胆大党 第二季", "type": "tv", "status": "done",
                        "create_time": "2026-01-19 06:42:11", "tmdb_id": "240411", "season": 2}]
    sequel = _simkl_show(2665178, "Dan Da Dan", {"mal": "60543"}, {1: [(1, BAD)]}, kind="anime")
    actions = build_repair_plan([sequel], douban, WINDOW, manual_map={2665178: "dd2"})
    a = actions[0]
    assert a["skip"] is False
    assert a["readd"][0]["season"] == 1
    assert a["readd"][0]["watched_at"] == "2026-01-18T22:42:11Z"


def test_find_oct2_faulty_shows_preserves_thick_of_it_and_35_up():
    from douban2simkl.repair import find_oct2_faulty_shows

    items = [
        # Faulty Walking Dead from Oct 2
        {
            "_kind": "shows",
            "show": {"title": "The Walking Dead", "ids": {"simkl": 2090, "imdb": "tt1520211"}},
            "last_watched_at": "2026-10-02T17:14:38Z",
        },
        # User genuine watch: The Thick of It (ID 9538)
        {
            "_kind": "shows",
            "show": {"title": "The Thick of It", "ids": {"simkl": 9538, "imdb": "tt0459159"}},
            "last_watched_at": "2026-10-02T18:47:05Z",
        },
        # User genuine movie watch: 35 Up (if present in dump)
        {
            "_kind": "shows",
            "show": {"title": "35 Up", "ids": {"simkl": 77502}},
            "last_watched_at": "2026-10-02T16:07:51Z",
        },
        # An older show watched in September
        {
            "_kind": "shows",
            "show": {"title": "Severance", "ids": {"simkl": 12345}},
            "last_watched_at": "2026-09-15T10:00:00Z",
        },
    ]

    delete_list, preserved_list = find_oct2_faulty_shows(items)
    assert len(delete_list) == 1
    assert delete_list[0]["simkl_id"] == 2090
    assert delete_list[0]["title"] == "The Walking Dead"

    assert len(preserved_list) == 2
    preserved_ids = {p["simkl_id"] for p in preserved_list}
    assert preserved_ids == {9538, 77502}


def test_reset_local_sync_state(tmp_path):
    import sqlite3
    from douban2simkl.repair import reset_local_sync_state

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE imdb_cache (
            douban_id TEXT PRIMARY KEY,
            imdb_id TEXT,
            series_imdb_id TEXT,
            season INTEGER,
            title TEXT,
            updated_at REAL,
            tmdb_id TEXT,
            tvdb_id TEXT
        )
    """)
    c.execute("""
        CREATE TABLE sync_state (
            douban_id TEXT PRIMARY KEY,
            status TEXT,
            synced_at REAL
        )
    """)
    # Insert Walking Dead records
    c.execute("INSERT INTO imdb_cache (douban_id, imdb_id, series_imdb_id, tmdb_id, title) VALUES (?, ?, ?, ?, ?)",
              ("d1", "tt1520211", "tt1520211", "1402", "行尸走肉 第一季"))
    c.execute("INSERT INTO imdb_cache (douban_id, imdb_id, series_imdb_id, tmdb_id, title) VALUES (?, ?, ?, ?, ?)",
              ("d8", "tt6156390", "tt1520211", "1402", "行尸走肉 第八季"))
    # Insert unrelated show
    c.execute("INSERT INTO imdb_cache (douban_id, imdb_id, tmdb_id, title) VALUES (?, ?, ?, ?)",
              ("d99", "tt9999999", "99999", "Other Show"))

    # Both marked as synced
    c.execute("INSERT INTO sync_state VALUES ('d1', 'synced', 1.0)")
    c.execute("INSERT INTO sync_state VALUES ('d8', 'synced', 1.0)")
    c.execute("INSERT INTO sync_state VALUES ('d99', 'synced', 1.0)")
    conn.commit()
    conn.close()

    deleted_items = [
        {"simkl_id": 2090, "ids": {"simkl": 2090, "imdb": "tt1520211", "tmdb": "1402"}}
    ]

    reset_count = reset_local_sync_state(db_path, deleted_items)
    assert reset_count == 2

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT douban_id FROM sync_state")
    remaining = [r[0] for r in c.fetchall()]
    conn.close()

    assert remaining == ["d99"]

