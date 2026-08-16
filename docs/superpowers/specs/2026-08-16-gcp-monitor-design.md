# GCP 定时监控（Cloud Scheduler + Cloud Functions）— 设计文档

- 日期：2026-08-16
- 状态：设计已与用户确认，待实现
- 分支：`gcp-monitor`（从 main 分叉）

---

## 1. 背景与动机

GitHub Actions 的 `schedule` 是**尽力而为**：实测密集 5 分钟窗口被大量丢弃、运行迟到 20-30 分钟（官方文档明示"高峰会延迟、负载高时队列任务会被丢弃"）。无法满足"实时上新通知"。

切换到 **GCP serverless**：Cloud Scheduler（可靠分钟级 cron）+ Cloud Functions（Python 原生），**永久免费**、无子请求/CPU 限制、可部署东京低延迟。**用户决策：GCP 部署验证后直接切换，停用 GitHub Actions（无兜底）。**

---

## 2. 目标与非目标

**目标：**
1. 分钟级准时的定时触发（替代 GitHub Actions 的不可靠 schedule）
2. 永久免费（只用 Always Free 额度，非试用）
3. 复用现有采集/打分/通知逻辑（`ur_api`/`models`/`score`/`notify`/`costtime`）
4. 函数部署东京（低延迟到 UR/Discord），快照存储也免费

**非目标：**
- 不用 VM（用户选 serverless）
- 不做规律分析、不存轮询记录（沿用既有决策）
- 不改采集/打分逻辑本身，只改"运行器"（存储 + 触发 + 入口）

---

## 3. 关键事实（已核实）

| 项 | 结论 |
|---|---|
| Cloud Scheduler | **每计费账号 3 个免费 job**（我们用 2 个）；支持 `timeZone=Asia/Tokyo`；分钟级可靠 |
| Cloud Functions 2nd gen 免费档 | **200 万次调用 + 40 万 GB-s + 20 万 vCPU-s/月**（不限区域）——我们 ~1000 次/月，只占零头 |
| Firestore 免费档 | **1 GiB 存储 + 每天 5 万读/2 万写/2 万删**（**不限区域**）——快照 1 个文档、~1000 读 + ~30 写/月，绰绰有余 |
| Secret Manager 免费档 | 6 个 active secret + 1 万次访问/月 |
| GCS 免费档 | **限美国三区**（us-east1/west1/central1）——东京桶不免费，故快照用 Firestore |
| e2-micro 免费 VM | 美国区才免费——本方案不用 VM |
| Function 超时 | gen2 最长 60 分钟（我们单次 ~2-4 分钟，远超够用） |
| Budget Alert | 设 $1 阈值防任何意外扣费 |

---

## 4. 架构

```
Cloud Scheduler (2 个 job, 时区 Asia/Tokyo, OIDC 认证)
   │ HTTP POST + OIDC token(服务账号)
   ▼
Cloud Functions 2nd gen (Python 3.12, asia-northeast1 东京)
   │ gcp_monitor.py: 读配置→读 webhook(Secret Manager)→采集→打分→Discord→写快照
   ▼
Firestore (快照 rooms.json 单文档)   +   Discord webhook
```

**触发安全**：函数 `--no-allow-unauthenticated`（不公开），Cloud Scheduler 用服务账号 OIDC token 调用，该服务账号授予 `roles/cloudfunctions.invoker`。Webhook 存 Secret Manager，不进代码/仓库。

---

## 5. 详细设计

### 5.1 节奏（Cloud Scheduler 2 个 job，Asia/Tokyo 时区）

| Job | Cron (JST) | 说明 |
|---|---|---|
| `monitor-dense` | `25,30,35,40,45 10,12,14,16,18 * * *` | 上新槽密集 5 分钟（GCP 可靠，这次真的每 5 分钟都跑） |
| `monitor-hourly` | `7 8,9,11,13,15,17,19,20 * * *` | 每小时兜底（避开密集偶数小时，夜间不跑） |

一天 33 次运行。与 GitHub 版节奏完全一致。

### 5.2 函数 `functions/monitor/`（部署目录）

```
functions/monitor/
├── gcp_monitor.py        # HTTP handler(entry-point=monitor)
├── actions_monitor.py    # 复用, run() 加 store 抽象
├── ur_api.py / models.py / score.py / notify.py / costtime.py / config.py   # 原样复用
├── config.actions.yaml   # 打包(无 webhook)
└── requirements.txt      # requests, pyyaml, google-cloud-firestore, google-cloud-secret-manager
```

### 5.3 代码改造（最小化）

**`actions_monitor.py`（改）**：`run()` 加可选 `store` 参数（对象有 `load()`/`save(snapshot)`）：

```python
class FileStore:
    def __init__(self, path): self.path = path
    def load(self): return load_snapshot(self.path)
    def save(self, snapshot): save_snapshot(self.path, snapshot)

def run(cfg, api, snapshot_path=SNAPSHOT_PATH, notify_fn=None, store=None):
    if store is None:
        store = FileStore(snapshot_path)
    snapshot = store.load() or {}
    ...
    old = store.load()
    store.save(snapshot)
    ...
```

- 默认 `FileStore` → 现有行为不变（GitHub Actions 路径 + 全部既有测试照常）
- 其余函数（`diff_new`/`build_cond`/`_load_table`/`current_rooms`/`_enrich_new_rooms`/打分/通知）**零改动**

**`functions/monitor/gcp_monitor.py`（新）**：

```python
import json, os
from google.cloud import firestore, secretmanager
import actions_monitor as am
from config import load_config
from ur_api import UrApi

class FirestoreStore:
    def __init__(self):
        self.db = firestore.Client()
        self.doc = self.db.document("monitor", "snapshot")
    def load(self):
        snap = self.doc.get()
        return snap.to_dict() if snap.exists else None
    def save(self, snapshot):
        self.doc.set(snapshot)

def _webhook():
    client = secretmanager.SecretManagerServiceClient()
    name = (f"projects/{os.environ['GOOGLE_CLOUD_PROJECT']}"
            "/secrets/danchi-discord-webhook/versions/latest")
    return client.access_secret_version(request={"name": name}).payload.data.decode()

def monitor(request):
    cfg = load_config("config.actions.yaml")
    cfg.discord.webhook_url = _webhook()   # 注入 secret
    api = UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)
    stat = am.run(cfg, api, store=FirestoreStore())
    print(f"total={stat['total']} new={stat['new']} pushed={stat['pushed']}", flush=True)
    return json.dumps(stat), 200
```

### 5.4 复用与去重

| 模块 | 处理 |
|---|---|
| `ur_api`/`models`/`score`/`notify`/`costtime`/`config` | ✅ 原样复制进函数目录 |
| `actions_monitor.py` | ✅ run() 加 store 抽象；其余不动 |
| `_commit_snapshot`/`main()`（git 提交路径） | 保留（GitHub 路径过渡期仍用；GCP 不调用） |
| `db.py`/`monitor.py`/`schedule.py`（旧本地循环） | 不用 |

---

## 6. 边界与错误处理

沿用 `actions_monitor.py` 已实现的健壮性（全部继承到 GCP）：
- 首跑无快照 → 静默建基线不通知
- 房间详情/打分失败 → 不进快照 → 下次重试
- notify 失败 → 不进快照 → 下次重试（at-least-once）
- **快照存取失败**（Firestore 不可用）→ 函数异常退出，GCP 日志可见；下次调度重试
- webhook secret 读取失败 → 函数退出非零，日志可见（不会静默死）
- 403/429 UR 限流 → 复用指数退避；若被 Ban → 调低密集频率
- Cloud Scheduler 丢任务 → **比 GitHub 可靠得多，但非 SLA**；仍由快照-diff 结构兜底不漏

---

## 7. 测试

1. **既有测试不变**：`tests/test_actions_monitor.py`（含 top-X、失败重试等）继续通过——run() 默认走 FileStore
2. **新增**：`FirestoreStore` 的 load/save 用 fake client 单测；`monitor()` handler 冒烟（fake request）
3. **本地冒烟**：`python -c "from gcp_monitor import monitor; print(monitor(None))"`（无 GCP 时用 fake 或跳过）
4. **线上验证**：部署后手动触发函数一次 → 确认 `total>0 new=0 pushed=0 changed=True`（静默建基线）→ Firestore 有快照 → 触发第二次 `changed=False` → 再触发一次确认到 Discord 链路
5. **停 GitHub**：验证通过后 `gh workflow disable monitor.yml`

---

## 8. 部署步骤（gcloud CLI，用户在 WSL 驱动）

1. `gcloud auth login`（用户交互登录）
2. 建项目 + 开通计费（免费档也要绑卡）：
   `gcloud projects create danchi-monitor && gcloud config set project danchi-monitor`
   （计费在 console 开通或 `gcloud billing projects link`）
3. 启用 API：`gcloud services enable firestore.googleapis.com cloudfunctions.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com`
4. 建 Firestore 数据库（native mode，asia-northeast1）
5. 建 secret：`gcloud secrets create danchi-discord-webhook`（webhook 值取自本地 config.yaml，经管道注入不落 shell 历史）
6. 部署函数：`gcloud functions deploy monitor --gen2 --runtime python312 --trigger-http --no-allow-unauthenticated --region=asia-northeast1 --source=functions/monitor --entry-point=monitor --timeout=540 --memory=512MB`
7. 授予调度服务账号 `roles/cloudfunctions.invoker`
8. 建 2 个 scheduler job（OIDC 认证，Asia/Tokyo 时区，cron 见 5.1）
9. 建 Budget Alert（$1 阈值）→ 防意外扣费
10. 手动触发函数验证 → 通过后 `gh workflow disable monitor.yml` 停 GitHub

---

## 9. 部署与回滚

- **上线**：部署 → 手动触发验证 → 停 GitHub Actions（用户已定：直接切换，无兜底）
- **回滚**：`gcloud functions delete monitor` + 删 scheduler jobs → 重新启用 GitHub workflow（`gh workflow enable monitor.yml`）
- **成本护栏**：Budget Alert $1；只在免费档内用量；若超量（几乎不可能）会收到告警

---

## 10. 不做

- 不部署 VM、不碰 e2-micro
- 不改采集/打分逻辑、不迁移到 GCS（Firestore 免费且不限区域）
- GitHub Actions 代码保留在仓库（回滚路径），但上线后停用
