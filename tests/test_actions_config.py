from config import load_config

def test_actions_config_loads():
    cfg = load_config("config.actions.yaml")
    assert cfg.destination.station_cd == "2827"
    assert cfg.areas == ["01", "02", "03", "04", "05", "06"]
    assert cfg.precise.rent_max == 100000
    assert cfg.weights.commute == 30
    assert cfg.push_threshold == 0
    assert cfg.discord.webhook_url == ""      # 公共安全: 无 webhook
    assert cfg.discord.llm_comment is False
    assert cfg.schedule.day_interval_min == 10
