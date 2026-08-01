# tests/test_ur_api.py
import pytest
from ur_api import UrApi

def make_api(monkeypatch, resp):
    class FakeResp:
        def read(self): return resp if isinstance(resp, bytes) else resp.encode()
        # ur_api._post uses `with urlopen(...) as r:`, so the stub must be a context manager.
        def __enter__(self): return self
        def __exit__(self, *exc): return False
    def fake_open(req, timeout=30):
        assert "chintai.r6.ur-net.go.jp" in req.full_url
        return FakeResp()
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    return UrApi(user_agent="test", timeout=30, retry_max=2, backoff_base_sec=0)

def test_suggest_station(monkeypatch):
    api = make_api(monkeypatch, '[{"value":"2827","text":"浜松町"}]')
    res = api.suggest_station("浜松町")
    assert res[0]["value"] == "2827"

def test_get_room_list(monkeypatch):
    api = make_api(monkeypatch, '[{"id":"001080409","rent":"60,900円","type":"3DK"}]')
    rooms = api.get_room_list("20_2600", "2827", None, "tokyo")
    assert rooms[0]["id"] == "001080409"
