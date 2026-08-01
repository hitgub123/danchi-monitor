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
