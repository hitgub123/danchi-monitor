# tests/test_llm_comment.py
import llm_comment

def test_returns_empty_without_config(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert llm_comment.llm_comment(None) == ""

def test_calls_proxy(monkeypatch):
    from models import Room
    captured = {}
    def fake_post(url, headers=None, json=None, timeout=10):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        class R:
            status_code = 200
            def json(self):
                return {"content": [{"type": "text", "text": "值得看：位置好租金低"}]}
        return R()
    monkeypatch.setattr("requests.post", fake_post)
    r = Room("r1","20_2600","館ヶ丘","409号室","",60900,4500,"3DK",53.0,4,5,True,True,10,40,"tokyo","八王子市")
    out = llm_comment.llm_comment(r, base_url="http://127.0.0.1:15721",
                                  auth_token="tk", model="claude-sonnet-4-5")
    assert "值得看" in out
    assert captured["url"] == "http://127.0.0.1:15721/v1/messages"
    assert captured["headers"]["x-api-key"] == "tk"
