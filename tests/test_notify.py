# tests/test_notify.py
import json
import notify

def make_poster(monkeypatch, status=204, body=""):
    captured = {}
    def fake_post(url, json=None, timeout=10):
        captured["url"] = url
        captured["json"] = json
        class R:
            status_code = status
            text = body
        return R()
    monkeypatch.setattr("requests.post", fake_post)
    return captured

def test_notify_new_room(monkeypatch):
    from models import Room
    r = Room(room_id="r1", danchi_id="20_2600", danchi_name="館ヶ丘", name="409号室",
             url="https://www.ur-net.go.jp/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409",
             rent=60900, commonfee=4500, madori="3DK", area=53.0, floor=4, total_floors=5,
             has_elevator=True, renovated=True, walk_min=10, commute_min=40,
             prefecture="tokyo", skcs="八王子市")
    cap = make_poster(monkeypatch, 204)
    ok = notify.notify_new_room("https://discord.test/hook", r, 78.5, "步行10分")
    assert ok is True
    embed = cap["json"]["embeds"][0]
    assert "館ヶ丘" in embed["title"]
    assert any(f["name"] == "月租" for f in embed["fields"])
    assert cap["url"] == "https://discord.test/hook"

def test_notify_returns_false_on_error(monkeypatch):
    cap = make_poster(monkeypatch, 500, "boom")
    assert notify.send_discord("https://discord.test/hook", "t", [], 0) is False

def test_send_discord_normalizes_relative_url(monkeypatch):
    # 回归：Discord embed 的 url 必须完整; UR 返回的相对路径 /chintai/... 必须补全为 https://www.ur-net.go.jp/...
    cap = make_poster(monkeypatch, 204)
    ok = notify.send_discord("https://discord.test/hook", "t", [], 0,
                             "/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409")
    assert ok is True
    assert cap["json"]["embeds"][0]["url"] == \
        "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409"
    # 已是完整 URL 则原样保留
    cap2 = make_poster(monkeypatch, 204)
    notify.send_discord("https://discord.test/hook", "t", [], 0, "https://x.example/y")
    assert cap2["json"]["embeds"][0]["url"] == "https://x.example/y"
