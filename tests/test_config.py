# tests/test_config.py
from config import load_config

def test_load_config():
    cfg = load_config("config.yaml")
    assert cfg.destination.station_cd == "2827"
    assert cfg.destination.commute_max_min == 60
    assert cfg.precise.rent_max == 100000
    assert cfg.weights.commute == 30

def test_config_missing_file_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_config("nope.yaml")

def test_schedule_config_parsed():
    cfg = load_config("config.yaml")
    assert cfg.schedule.day_interval_min == 10
    assert cfg.schedule.night_interval_min == 60
    assert cfg.schedule.day_start_hour == 8
    assert cfg.schedule.day_end_hour == 22
    assert cfg.schedule.max_jitter_sec == 5
    assert cfg.schedule.dense_windows is not None
    assert cfg.schedule.dense_windows.hours == [10, 12, 14, 16, 18]
    assert cfg.schedule.dense_windows.start_min == 27
    assert cfg.schedule.dense_windows.end_min == 42
    assert cfg.schedule.dense_windows.interval_min == 3

def test_dense_windows_section_filters_unknown_keys():
    from config import _section, DenseWindows
    dw = _section(DenseWindows, {"hours": [10], "start_min": 27, "end_min": 42, "interval_min": 3, "stray": 1})
    assert dw.hours == [10]
    assert dw.start_min == 27
    assert dw.interval_min == 3
    assert not hasattr(dw, "stray")
