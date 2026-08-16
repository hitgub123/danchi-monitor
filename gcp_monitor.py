# gcp_monitor.py — Cloud Functions 2nd gen 入口(HTTP trigger, Python 3.12)
# 复用 actions_monitor 的采集/打分/通知逻辑; 快照存 Firestore, webhook 走 Secret Manager。
import json
import os

import actions_monitor as am
from config import load_config
from ur_api import UrApi


class FirestoreStore:
    """快照存 Firestore 单文档(免费档 1GiB 存储 + 5万读/2万写每天, 不限区域)。
    仅在未提供 client 时才 import firestore(测试可传 fake client 免装依赖)。"""
    def __init__(self, client=None):
        if client is None:
            from google.cloud import firestore
            client = firestore.Client()
        self.db = client
        self.doc = self.db.document("monitor", "snapshot")

    def load(self):
        snap = self.doc.get()
        return snap.to_dict() if snap.exists else None

    def save(self, snapshot):
        self.doc.set(snapshot)


def _webhook():
    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()
    name = (f"projects/{os.environ['GOOGLE_CLOUD_PROJECT']}"
            "/secrets/danchi-discord-webhook/versions/latest")
    return client.access_secret_version(request={"name": name}).payload.data.decode("utf-8")


def monitor(request):
    """Cloud Scheduler 触发入口。返回 JSON stat。request 未使用(定时任务无需输入)。"""
    cfg = load_config("config.actions.yaml")
    cfg.discord.webhook_url = _webhook()   # 注入 Secret Manager 的 webhook
    api = UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)
    stat = am.run(cfg, api, store=FirestoreStore())
    print(f"total={stat['total']} new={stat['new']} pushed={stat['pushed']} changed={stat['changed']}",
          flush=True)
    return json.dumps(stat), 200
