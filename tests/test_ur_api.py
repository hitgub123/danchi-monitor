# tests/test_ur_api.py
import logging
import urllib.error

import pytest
from ur_api import UrApi, RateLimitedError

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

# ---- F8：danchi_id 格式守卫 ----

def test_get_room_detail_malformed_danchi_id(monkeypatch, caplog):
    # "abc" 过短，旧代码 danchi_id[6] 会 IndexError → 团地每轮静默跳过
    api = make_api(monkeypatch, "[]")
    with caplog.at_level(logging.WARNING, logger="ur_api"):
        res = api.get_room_detail("abc", "r1")
    assert res == {}
    assert any("danchi_id" in r.message for r in caplog.records)

def test_get_danchi_detail_malformed_danchi_id(monkeypatch, caplog):
    api = make_api(monkeypatch, "[]")
    with caplog.at_level(logging.WARNING, logger="ur_api"):
        res = api.get_danchi_detail("abc")
    assert res == {}
    assert any("danchi_id" in r.message for r in caplog.records)

def test_get_room_detail_valid_id_still_works(monkeypatch):
    api = make_api(monkeypatch, '[{"year":"20","facility":"エレベーター"}]')
    res = api.get_room_detail("20_2600", "001080409")
    assert res["year"] == "20"
    assert res["facility"] == "エレベーター"

# ---- F2：_get 与 _post 一致的退避重试 ----

def _ok_response(data=b"<xml/>"):
    class FakeResp:
        def read(self): return data
        def __enter__(self): return self
        def __exit__(self, *exc): return False
    return FakeResp()

def test_get_retries_on_429_then_succeeds(monkeypatch):
    calls = []
    def fake_open(req, timeout=30):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, None)
        return _ok_response()
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    api = UrApi(user_agent="test", timeout=30, retry_max=3, backoff_base_sec=0)
    assert api._get("https://www.ur-net.go.jp/x.xml") == b"<xml/>"
    assert len(calls) == 3  # 重试 2 次后成功

def test_get_raises_rate_limited_when_retries_exhausted(monkeypatch):
    def fake_open(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    api = UrApi(user_agent="test", timeout=30, retry_max=2, backoff_base_sec=0)
    with pytest.raises(RateLimitedError):
        api._get("https://www.ur-net.go.jp/x.xml")
