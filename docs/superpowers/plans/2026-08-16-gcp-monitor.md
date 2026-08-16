# GCP 定时监控（Cloud Scheduler + Cloud Functions）— Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 UR 上新监控迁移到 GCP serverless（Cloud Scheduler 可靠分钟级触发 + Cloud Functions 东京跑 Python），永久免费，替代 GitHub Actions 不可靠的 schedule。

**Architecture:** `actions_monitor.py` 的 `run()` 加"可注入快照存储"抽象（默认 `FileStore` 保持现有行为）；新增 `gcp_monitor.py`（Cloud Functions HTTP 入口：读 Secret Manager webhook → 采集打分 → Discord → 写 Firestore 快照）；`deploy.sh` 从仓库根复制单源模块到临时构建目录再 `gcloud functions deploy`，避免模块重复。Cloud Scheduler 2 个 job（Asia/Tokyo 时区）经 OIDC 认证触发函数。

**Tech Stack:** Python 3.12（Cloud Functions 2nd gen），`requests`/`pyyaml`/`google-cloud-firestore`/`google-cloud-secret-manager`。本地测试：pytest（venv）。

## Global Constraints

- 新分支 `gcp-monitor`（已从 main 创建）；所有提交只在此分支。
- **不修改** `ur_api.py`/`models.py`/`score.py`/`notify.py`/`costtime.py`/`config.py`（原样复用）；`actions_monitor.py` 只加 `FileStore` 类 + `run()` 的 `store` 参数，其余逻辑不动。
- `gcp_monitor.py` + `requirements.function.txt` + `deploy.sh` 放仓库根；函数目录由 `deploy.sh` 构建（单代码源，不提交重复副本）。
- 只用 GCP 免费档资源：Cloud Scheduler 3 job 内（用 2）、Cloud Functions 免费档、Firestore 1GiB、Secret Manager 6 个内。**不碰 VM / 静态 IP / GCS 东京桶**。
- 快照存 **Firestore 单文档** `monitor/snapshot`（免费且不限区域）；webhook 存 **Secret Manager** `danchi-discord-webhook`（不进代码/仓库）。
- 函数 `--no-allow-unauthenticated`（不公开），Scheduler 用服务账号 OIDC 认证。
- 工作区有用户未跟踪文件 `show_current.py`，**不要碰**；每个 commit 只 `git add` 任务文件，禁止 `git add -A`/`git add .`。
- 每任务 TDD：先写失败测试 → 跑确认失败 → 最小实现 → 跑确认通过 → 提交。
- 测试命令：`.venv/bin/python -m pytest <path> -v`。
- **Task 3（部署）在执行前要求用户已完成**：`gcloud auth login` + 项目计费开通（用户手动部分）。

---

### Task 1: `actions_monitor.py` 快照存储抽象（FileStore + store 参数）

**Files:**
- Modify: `actions_monitor.py`（加 `FileStore` 类；`run()` 加 `store` 参数）
- Test: `tests/test_actions_monitor.py`（追加 2 个测试）

**Interfaces:**
- Produces: `actions_monitor.FileStore(path)`（有 `load()` → dict|None、`save(snapshot)`）；`actions_monitor.run(cfg, api, snapshot_path=SNAPSHOT_PATH, notify_fn=None, store=None)`——`store=None` 时用 `FileStore(snapshot_path)`，行为与现有一致。
- Consumes: 既有 `load_snapshot`/`save_snapshot`。Task 2 的 `gcp_monitor.FirestoreStore` 实现同一 `load()/save()` 接口并传入 `run()`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_actions_monitor.py` **末尾追加**：

```python
# ---- 快照存储抽象(FileStore / 可注入 store) ----

class FakeStore:
    def __init__(self, initial=None):
        self.data = initial
        self.loads = 0
        self.saves = 0
    def load(self):
        self.loads += 1
        return self.data
    def save(self, snapshot):
        self.saves += 1
        self.data = snapshot

def test_run_uses_injectable_store():
    cfg = make_cfg()
    store = FakeStore()
    api = FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL)
    stat = am.run(cfg, api, store=store, notify_fn=lambda *a, **k: True)
    assert stat["total"] == 1 and stat["new"] == 0 and stat["pushed"] == 0
    assert store.loads >= 2 and store.saves >= 1          # 开头 load + changed 判定 load, 末尾 save
    assert "001080409" in store.data["rooms"]
    assert "20_2600" in store.data["danchi_static"]

def test_run_default_store_still_writes_file(tmp_path):
    p = str(tmp_path / "rooms.json")
    am.run(make_cfg(), FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL), str(p),
           notify_fn=lambda *a, **k: True)
    assert "001080409" in am.load_snapshot(p)["rooms"]
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_actions_monitor.py::test_run_uses_injectable_store tests/test_actions_monitor.py::test_run_default_store_still_writes_file -v`
Expected: FAIL —— `TypeError: run() got an unexpected keyword argument 'store'`

- [ ] **Step 3: 实现**

`actions_monitor.py` 改动（在 `save_snapshot` 之后加类，`run()` 签名与开头改）：

```python
class FileStore:
    """默认快照存储：写本地 JSON 文件（GitHub Actions / 本地调试路径）。"""
    def __init__(self, path):
        self.path = path
    def load(self):
        return load_snapshot(self.path)
    def save(self, snapshot):
        save_snapshot(self.path, snapshot)
```

`run()` 签名改为 `def run(cfg, api, snapshot_path=SNAPSHOT_PATH, notify_fn=None, store=None):`，函数开头改为：

```python
    if notify_fn is None:
        notify_fn = notify.notify_new_room
    if store is None:
        store = FileStore(snapshot_path)
    webhook = getattr(getattr(cfg, "discord", None), "webhook_url", "")
    snapshot = store.load() or {}
```

并把函数尾部两行：
```python
    old = load_snapshot(snapshot_path)
    save_snapshot(snapshot_path, snapshot)
```
改为：
```python
    old = store.load()
    store.save(snapshot)
```
其余逻辑（table/danchi_static/rooms/diff/enrich/top-X）**一律不动**。

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_actions_monitor.py -v`
Expected: 全部 PASS（原 13 个 + 新 2 个）

- [ ] **Step 5: 提交**

```bash
git add actions_monitor.py tests/test_actions_monitor.py
git commit -m "refactor: actions_monitor.run() 快照存储抽象(FileStore + 可注入 store)"
```

---

### Task 2: `gcp_monitor.py` + 函数依赖 + `deploy.sh`

**Files:**
- Create: `gcp_monitor.py`（Cloud Functions 入口）
- Create: `requirements.function.txt`（函数依赖）
- Create: `deploy.sh`（构建 + 部署脚本；Task 3 执行）
- Test: `tests/test_gcp_monitor.py`

**Interfaces:**
- Consumes: Task 1 的 `actions_monitor.run(..., store=...)`；既有模块（`ur_api`/`models`/`score`/`notify`/`costtime`/`config`）。
- Produces: `gcp_monitor.monitor(request) -> (json_str, int)`（HTTP handler，entry-point）；`gcp_monitor.FirestoreStore`（实现 Task 1 的 `load()/save()` 接口）；`gcp_monitor._webhook() -> str`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_gcp_monitor.py`：

```python
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
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_gcp_monitor.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'gcp_monitor'`

- [ ] **Step 3: 实现**

创建 `gcp_monitor.py`：

```python
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
```

创建 `requirements.function.txt`（部署时改名为 requirements.txt）：

```
requests
pyyaml
google-cloud-firestore
google-cloud-secret-manager
```

创建 `deploy.sh`（构建临时函数目录并部署；**Task 3 执行**）：

```bash
#!/bin/bash
# 从仓库根复制单源模块到临时构建目录, 再 gcloud functions deploy。
set -euo pipefail
PROJECT="${GCP_PROJECT:-danchi-monitor}"
REGION="asia-northeast1"
BUILD_DIR=".build/function"
rm -rf "$BUILD_DIR"; mkdir -p "$BUILD_DIR"
cp gcp_monitor.py actions_monitor.py ur_api.py models.py score.py notify.py costtime.py config.py config.actions.yaml "$BUILD_DIR"/
cp requirements.function.txt "$BUILD_DIR/requirements.txt"
gcloud config set project "$PROJECT"
gcloud functions deploy monitor \
  --gen2 --runtime python312 --trigger-http --no-allow-unauthenticated \
  --region="$REGION" --source="$BUILD_DIR" --entry-point=monitor \
  --timeout=540 --memory=512MB
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_gcp_monitor.py -v`
Expected: 2 个测试全 PASS

- [ ] **Step 5: 提交**

```bash
git add gcp_monitor.py requirements.function.txt deploy.sh tests/test_gcp_monitor.py
git commit -m "feat: GCP Cloud Functions 入口(Firestore 快照 + Secret 注入) + 部署脚本"
```

---

### Task 3: GCP 部署与验证（需用户已完成 gcloud auth + 计费）

**前置（用户手动）**：`gcloud auth login` 已完成；项目 `danchi-monitor` 已创建并开通计费（绑定支付方式）。执行前先确认：
```bash
gcloud auth list        # 应显示已登录账号
gcloud config get-value project   # 应为 danchi-monitor
```

**Files:** 无代码改动；执行 `deploy.sh` + 下列 gcloud 命令（本任务由控制器直接驱动，不走 subagent——需要用户交互与实时判断）。

- [ ] **Step 1: 启用 API**

```bash
gcloud services enable firestore.googleapis.com cloudfunctions.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com
```

- [ ] **Step 2: 建 Firestore 数据库（native mode）**

```bash
gcloud firestore databases create --location=asia-northeast1
```

- [ ] **Step 3: 建 webhook secret（从本地 config.yaml 取 webhook 值, 不落 shell 历史）**

```bash
gcloud secrets create danchi-discord-webhook --replication-policy=user-managed --locations=asia-northeast1
.venv/bin/python -c "import yaml; print(yaml.safe_load(open('config.yaml'))['discord']['webhook_url'])" \
  | gcloud secrets versions add danchi-discord-webhook --data-file=-
```

- [ ] **Step 4: 部署函数（构建临时目录 + deploy）**

```bash
chmod +x deploy.sh && GCP_PROJECT=danchi-monitor ./deploy.sh
```
Expected: `gcloud functions deploy` 成功，输出函数 URL：`https://asia-northeast1-danchi-monitor.cloudfunctions.net/monitor`

- [ ] **Step 5: 授权调度服务账号 invoker**

用默认 App Engine 服务账号（`<project>@appspot.gserviceaccount.com`）作为 Scheduler 的 OIDC 身份，授予 invoker：
```bash
SA="danchi-monitor@appspot.gserviceaccount.com"
gcloud functions add-iam-policy-binding monitor --region=asia-northeast1 \
  --member="serviceAccount:${SA}" --role=roles/cloudfunctions.invoker
```

- [ ] **Step 6: 建 2 个 Cloud Scheduler job（Asia/Tokyo 时区, OIDC 认证）**

```bash
FUNC_URL=$(gcloud functions describe monitor --region=asia-northeast1 --format='value(httpsTrigger.url)')
gcloud scheduler jobs create http monitor-dense \
  --schedule="25,30,35,40,45 10,12,14,16,18 * * *" --time-zone=Asia/Tokyo \
  --uri="$FUNC_URL" --http-method=POST \
  --oidc-service-account-email="$SA" --oidc-token-audience="$FUNC_URL"
gcloud scheduler jobs create http monitor-hourly \
  --schedule="7 8,9,11,13,15,17,19,20 * * *" --time-zone=Asia/Tokyo \
  --uri="$FUNC_URL" --http-method=POST \
  --oidc-service-account-email="$SA" --oidc-token-audience="$FUNC_URL"
```

- [ ] **Step 7: Budget Alert（$1 阈值, 防意外扣费）**

```bash
BA=$(gcloud billing accounts list --format='value(ACCOUNT_ID)' | head -1)
gcloud billing budgets create --billing-account="$BA" --display-name=monitor \
  --budget-amount=1.00 --filter-projects=projects/danchi-monitor \
  --threshold-rule=percent=0.5
```

- [ ] **Step 8: 验证（首跑建基线 → 二次 changed=False → 通知链路）**

```bash
# 手动触发(带调用者身份认证)
gcloud functions call monitor --region=asia-northeast1 --data '{}'
```
Expected 第 1 次：`{"total": 90+, "new": 0, "pushed": 0, "changed": true}`（静默建基线）→ 检查 Firestore `monitor/snapshot` 有数据：
```bash
gcloud firestore documents get monitor/snapshot --project=danchi-monitor  # 或 console 查看
```
再触发一次，Expected：`{"total": ..., "new": 0, "pushed": 0, "changed": false}`（无重复提交）。
最后手动往 webhook 发一条测试 Discord 消息确认链路可用（或用真实上新验证——等下次上新的房间出现）。

- [ ] **Step 9: 确认 GitHub Actions 已停 + 收尾**

```bash
gh workflow list   # 应显示 danchi-monitor disabled_manually(用户已停)
git push -u origin gcp-monitor
```
将本次改动推送到 gcp-monitor 分支（是否合并 main 由用户确认后再说）。

---

### 回滚（文档, 非任务步骤）

- 停 GCP：`gcloud functions delete monitor --region=asia-northeast1` + `gcloud scheduler jobs delete monitor-dense monitor-hourly`。
- 恢复 GitHub：`gh workflow enable monitor.yml`（快照文件仍在仓库, GitHub 版直接可用）。
