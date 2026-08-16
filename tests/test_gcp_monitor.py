import json
import gcp_monitor


class FakeFirestoreDoc:
    def __init__(self, data=None):
        self._data = data
        self.exists = data is not None
    def get(self):
        return self
    def to_dict(self):
        return self._data
    def set(self, snapshot):
        self._data = snapshot
        self.exists = True

class FakeDb:
    def __init__(self, data=None):
        self._doc = FakeFirestoreDoc(data)
    def document(self, *path):
        return self._doc

def test_firestore_store_roundtrip():
    store = gcp_monitor.FirestoreStore(client=FakeDb())
    assert store.load() is None
    snap = {"rooms": {"r1": {}}, "danchi_static": {}, "table": {}}
    store.save(snap)
    assert store.load() == snap

class FakeStore:
    def load(self): return None
    def save(self, snapshot): pass

def test_monitor_handler_runs(monkeypatch):
    captured = {}
    def fake_run(cfg, api, store=None, notify_fn=None):
        captured["cfg"] = cfg
        captured["store"] = store
        return {"total": 1, "new": 0, "pushed": 0, "changed": True}
    monkeypatch.setattr(gcp_monitor, "FirestoreStore", FakeStore)
    monkeypatch.setattr(gcp_monitor, "_webhook", lambda: "https://discord.test/hook")
    monkeypatch.setattr(gcp_monitor.am, "run", fake_run)
    body, status = gcp_monitor.monitor(None)
    assert status == 200
    assert json.loads(body)["total"] == 1
    assert captured["cfg"].discord.webhook_url == "https://discord.test/hook"
    assert isinstance(captured["store"], FakeStore)
