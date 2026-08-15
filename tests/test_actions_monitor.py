import actions_monitor as am

def test_load_snapshot_missing_returns_none(tmp_path):
    assert am.load_snapshot(str(tmp_path / "nope.json")) is None

def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "rooms.json")
    snap = {"table": {"2354": [2, 0]}, "danchi_static": {"20_2600": {"commute_min": 2}},
            "rooms": {"001080409": {"danchi_id": "20_2600"}}}
    am.save_snapshot(p, snap)
    assert am.load_snapshot(p) == snap

def test_load_snapshot_normalizes_missing_keys(tmp_path):
    p = str(tmp_path / "rooms.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"rooms": {}}')
    s = am.load_snapshot(p)
    assert s["rooms"] == {}
    assert s["table"] == {} and s["danchi_static"] == {}

def test_diff_new_baseline_empty():
    assert am.diff_new({"a": 1}, {}) == {}
    assert am.diff_new({}, {}) == {}

def test_diff_new_finds_new_only():
    cur = {"a": {"danchi_id": "x"}, "b": {"danchi_id": "y"}}
    prev = {"a": {"danchi_id": "x"}}
    assert am.diff_new(cur, prev) == {"b": {"danchi_id": "y"}}

def test_build_cond():
    assert am.build_cond("2827", {"2354": (2, 0), "1000": (30, 1)}, 60, 2) == "2827,1000,2354"
