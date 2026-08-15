# GitHub Actions 实时上新监控 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个 GitHub Actions 工作流，云端定时抓取 UR 在架房，用快照-diff 检测上新并推送 Discord，实现不依赖本机开机的实时监控。

**Architecture:** 新增 `actions_monitor.py`（无状态入口脚本：采集全量在架房 → 与 `snapshot/rooms.json` diff → 上新打分推送 → 覆盖快照，有变化才 commit）；`.github/workflows/monitor.yml` 用 cron 调度（密集窗口 5 分 + 全天 30 分兜底）+ `workflow_dispatch` 手动触发；`config.actions.yaml` 为公共安全配置（无 webhook）。复用 `ur_api`/`models`/`score`/`notify`/`costtime`/`config`，不修改它们。

**Tech Stack:** Python 3.12（stdlib + requests + pyyaml），GitHub Actions。测试：pytest 9.1.1（venv）。

## Global Constraints

- **新分支 `gh-actions-monitor`**，从 main 分叉；所有提交只在这个分支。**不碰 main、不改动任何既有模块**（`ur_api.py`/`models.py`/`score.py`/`notify.py`/`costtime.py`/`config.py`/`db.py`/`main.py` 等全部原样保留）。
- **仓库 PUBLIC**：`config.actions.yaml` 的 `discord.webhook_url` 必须是空字符串；真实 webhook 只经环境变量 `DANCHI_DISCORD_WEBHOOK`（Actions secret）注入（`config.py` 已支持 env 覆盖）。
- 快照 `snapshot/rooms.json` 存最新版全量在架房 + danchi 静态缓存 + cost-time 表；**只在内容变化时 commit**。
- `load_config("config.actions.yaml")` 要求 `config.actions.yaml` 含全部顶层键（destination/prefectures/areas/wide_filter/precise/weights/baseline/push_threshold/schedule/discord/http），schedule 段最少要有 `day_interval_min`+`night_interval_min`（`Schedule` dataclass 无默认的字段）。
- 工作区有用户未跟踪文件 `show_current.py`，**绝对不要碰**；每个 commit 只 `git add` 任务文件，禁止 `git add -A`/`git add .`。
- 每任务 TDD：先写失败测试 → 跑确认失败 → 最小实现 → 跑确认通过 → 提交。
- 测试命令：`.venv/bin/python -m pytest <path> -v`。

---

### Task 1: `config.actions.yaml`（公共安全配置）

**Files:**
- Create: `config.actions.yaml`
- Test: `tests/test_actions_config.py`

**Interfaces:**
- Produces: `config.actions.yaml` —— `load_config("config.actions.yaml")` 返回完整 `Config`，`cfg.discord.webhook_url == ""`。Task 2/3 的 `actions_monitor.py` 用它加载配置。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_actions_config.py`：

```python
from config import load_config

def test_actions_config_loads():
    cfg = load_config("config.actions.yaml")
    assert cfg.destination.station_cd == "2827"
    assert cfg.areas == ["01", "02", "03", "04", "05", "06"]
    assert cfg.precise.rent_max == 100000
    assert cfg.weights.commute == 30
    assert cfg.push_threshold == 40
    assert cfg.discord.webhook_url == ""      # 公共安全: 无 webhook
    assert cfg.discord.llm_comment is False
    assert cfg.schedule.day_interval_min == 10
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_actions_config.py -v`
Expected: FAIL —— `FileNotFoundError: config.actions.yaml`

- [ ] **Step 3: 实现**

创建 `config.actions.yaml`（评分参数与本地 `config.yaml` 一致；**webhook 留空**；schedule 段最小化）：

```yaml
destination:
  station_name: "浜松町"
  station_cd: "2827"
  commute_max_min: 60
  change_max: 2

prefectures: ["tokyo"]
areas: ["01", "02", "03", "04", "05", "06"]

wide_filter:
  rent_max: 110000
  walk_max: 15
  area_min: 35

precise:
  rent_max: 100000
  area_min: 40
  walk_max: 15
  walk_ideal: 10
  elevator_min_floor: 3
  renovated_keywords: ["リフォーム", "リノベーション", "リフォーム済み"]

weights:
  commute: 30
  walk: 20
  rent: 20
  area: 15
  room_type: 5
  floor: 5
  tokyo: 5

baseline:
  rent: 95000
  area: 43
  walk: 2
  commute: 30
  madori: "1DK"
  western: false
  floor: 5

push_threshold: 40

schedule:
  day_interval_min: 10
  night_interval_min: 60

discord:
  webhook_url: ""          # webhook 走 Actions secret(DANCHI_DISCORD_WEBHOOK); 仓库 PUBLIC, 绝不写真值
  llm_comment: false

http:
  user_agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
  timeout: 30
  retry_max: 3
  backoff_base_sec: 2
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_actions_config.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add config.actions.yaml tests/test_actions_config.py
git commit -m "feat: config.actions.yaml 公共安全配置(Actions 用, webhook 留空)"
```

---

### Task 2: `actions_monitor.py` 辅助函数（快照/diff/cond）

**Files:**
- Create: `actions_monitor.py`（本任务只写辅助函数部分）
- Test: `tests/test_actions_monitor.py`

**Interfaces:**
- Produces:
  - `load_snapshot(path: str) -> Optional[dict]`（不存在/损坏返回 `None`；否则返回含 `table`/`danchi_static`/`rooms` 三键的 dict）
  - `save_snapshot(path: str, snapshot: dict) -> None`
  - `diff_new(current: dict, previous: dict) -> dict`（返回 `previous` 中没有的 `{room_id: info}`；`previous` 空 → `{}` 静默基线）
  - `build_cond(dest_cd: str, table: dict, cost_max: int, change_max: int) -> str`（经 `costtime.build_station_condition`）
  - `_load_table(api, cfg, snapshot: dict) -> dict`（快照缓存命中则用，否则下载 XML 解析并入快照）
- Consumes: `config.actions.yaml`（Task 1）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_actions_monitor.py`：

```python
import actions_monitor as am

def test_load_snapshot_missing_returns_none(tmp_path):
    assert am.load_snapshot(str(tmp_path / "nope.json")) is None

def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "rooms.json")
    snap = {"table": {"2354": [2, 0]}, "danchi_static": {"20_2600": {"commute_min": 2}},
            "rooms": {"001080409": {"danchi_id": "20_2600"}}}
    am.save_snapshot(p, snap)
    assert am.load_snapshot(p) == snap

def test_load_snapshot_normalizes_missing_keys(tmp_path):
    p = str(tmp_path / "rooms.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"rooms": {}}')
    s = am.load_snapshot(p)
    assert s["rooms"] == {}
    assert s["table"] == {} and s["danchi_static"] == {}

def test_diff_new_baseline_empty():
    assert am.diff_new({"a": 1}, {}) == {}
    assert am.diff_new({}, {}) == {}

def test_diff_new_finds_new_only():
    cur = {"a": {"danchi_id": "x"}, "b": {"danchi_id": "y"}}
    prev = {"a": {"danchi_id": "x"}}
    assert am.diff_new(cur, prev) == {"b": {"danchi_id": "y"}}

def test_build_cond():
    assert am.build_cond("2827", {"2354": (2, 0), "1000": (30, 1)}, 60, 2) == "2827,1000,2354"
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_actions_monitor.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'actions_monitor'`

- [ ] **Step 3: 实现**

创建 `actions_monitor.py`（本任务只写这部分）：

```python
# actions_monitor.py — GitHub Actions 实时上新监控
# 每次运行: 抓全量在架房 → 快照 diff → 上新打分推送 Discord → 覆盖快照(有变化才 commit)
import json
import os
import subprocess
import sys

import costtime
import models as M
import notify
import score as S
from config import load_config
from ur_api import UrApi

SNAPSHOT_PATH = "snapshot/rooms.json"


# ---- 快照读写 ----

def load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("table", {})
        data.setdefault("danchi_static", {})
        data.setdefault("rooms", {})
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_snapshot(path, snapshot):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


# ---- diff / 基线 ----

def diff_new(current, previous):
    """上一快照中没有的新房间 {room_id: info}；previous 空(首跑) → {} 静默基线。"""
    if not previous:
        return {}
    return {rid: info for rid, info in current.items() if rid not in previous}


# ---- 通勤表: cond 由 table 派生; table 静态缓存, 避免每次重下 185KB XML ----

def build_cond(dest_cd, table, cost_max, change_max):
    return costtime.build_station_condition(dest_cd, table, cost_max, change_max)


def _load_table(api, cfg, snapshot):
    if snapshot.get("table"):
        return snapshot["table"]
    xml = api.get_cost_time_xml(cfg.destination.station_cd)
    table = costtime.parse_cost_time(xml, cfg.destination.commute_max_min, cfg.destination.change_max)
    snapshot["table"] = table
    return table
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_actions_monitor.py -v`
Expected: 6 个测试全 PASS

- [ ] **Step 5: 提交**

```bash
git add actions_monitor.py tests/test_actions_monitor.py
git commit -m "feat: actions_monitor 快照读写/diff/通勤表缓存"
```

---

### Task 3: `actions_monitor.py` 采集与编排（current_rooms / run / commit）

**Files:**
- Modify: `actions_monitor.py`（追加 `current_rooms`/`_build_room`/`run`/`_commit_snapshot`/`main`）
- Test: `tests/test_actions_monitor.py`（追加集成测试）

**Interfaces:**
- Consumes: Task 2 的 `load_snapshot`/`save_snapshot`/`diff_new`/`build_cond`/`_load_table`；`config.actions.yaml`。
- Produces:
  - `current_rooms(api, cfg, cond, table, danchi_static) -> dict`（`{room_id: {danchi_id, name, url, rent, commonfee, madori, area, floor}}`；顺带把新团地的 `commute_min/has_elevator/walk_min/name/skcs` 写入 `danchi_static`）
  - `run(cfg, api, snapshot_path=SNAPSHOT_PATH, notify_fn=None) -> dict`（返回 `{"total", "new", "pushed", "changed"}`）
  - `_commit_snapshot(path, room_count) -> bool`
  - `main() -> int`

- [ ] **Step 1: 写失败测试**

在 `tests/test_actions_monitor.py` **末尾追加**（并在文件顶部 import 处补 `from config import load_config`）：

```python
from config import load_config

XML = ('<?xml version="1.0" encoding="euc-jp"?><trainDoc><stationList>'
       '<stationTo code="2354"><costTime>2</costTime><changeTimes>0</changeTimes></stationTo>'
       '</stationList></trainDoc>').encode("euc-jp")

class FakeApi:
    def __init__(self, danchi_by_area, rooms_by_danchi, details):
        self._d = danchi_by_area; self._r = rooms_by_danchi; self._det = details
    def get_cost_time_xml(self, cd):
        return XML
    def suggest_station(self, name):
        return [{"value": "2354", "text": name}]
    def get_danchi_list(self, area, cond, wide, pref):
        return self._d.get(area, [])
    def get_room_list(self, danchi_id, cond, wide, pref):
        return self._r.get(danchi_id, [])
    def get_room_detail(self, danchi_id, room_id):
        return self._det.get((danchi_id, room_id), {})
    def get_danchi_detail(self, danchi_id):
        return {"facility": "エレベーター"}

DANCHI = [{"id": "20_2600", "name": "館ヶ丘", "skcs": "八王子市", "roomCount": 1,
           "access": "<li>JR中央線「高尾」駅 徒歩10分</li>"}]
ROOMS = [{"id": "001080409", "rent": "60,900円", "type": "3DK", "floorspace": "53㎡", "floor": "4階",
          "urlDetail": "/chintai/kanto/tokyo/20_2600_room.html?JKSS=001080409"}]
DETAIL = {("20_2600", "001080409"): {"year": "20", "floor": "4階 /5階", "facility": "エレベーター、リフォーム"}}

def make_cfg():
    return load_config("config.actions.yaml")

def test_run_baseline_silent(tmp_path):
    api = FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL)
    cfg = make_cfg()
    called = []
    stat = am.run(cfg, api, str(tmp_path / "rooms.json"),
                  notify_fn=lambda url, room, score, reason: called.append(room.room_id) or True)
    assert stat["total"] == 1 and stat["new"] == 0 and stat["pushed"] == 0
    assert called == []
    s = am.load_snapshot(str(tmp_path / "rooms.json"))
    assert "001080409" in s["rooms"]

def test_run_detects_and_pushes_new(tmp_path):
    p = str(tmp_path / "rooms.json")
    api1 = FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL)
    am.run(make_cfg(), api1, p, notify_fn=lambda *a, **k: True)   # 建基线
    rooms2 = ROOMS + [{"id": "0020304", "rent": "70,000円", "type": "2DK", "floorspace": "45㎡",
                       "floor": "2階", "urlDetail": "/chintai/kanto/tokyo/20_2600_room.html?JKSS=0020304"}]
    detail2 = dict(DETAIL); detail2[("20_2600", "0020304")] = {"year": "15", "floor": "2階 /5階",
                                                               "facility": "エレベーター、リフォーム"}
    api2 = FakeApi({"01": DANCHI}, {"20_2600": rooms2}, detail2)
    called = []
    stat = am.run(make_cfg(), api2, p, notify_fn=lambda url, room, score, reason: called.append(room.room_id) or True)
    assert stat["new"] == 1 and stat["pushed"] == 1 and called == ["0020304"]
    s = am.load_snapshot(p)
    assert set(s["rooms"]) == {"001080409", "0020304"}

def test_detail_failure_skips_snapshot_and_retries(tmp_path):
    p = str(tmp_path / "rooms.json")
    am.run(make_cfg(), FakeApi({"01": DANCHI}, {"20_2600": ROOMS}, DETAIL), p,
           notify_fn=lambda *a, **k: True)   # 基线只有 001080409
    rooms2 = ROOMS + [{"id": "0020304", "rent": "70,000円", "type": "2DK", "floorspace": "45㎡",
                       "floor": "2階", "urlDetail": "/chintai/kanto/tokyo/20_2600_room.html?JKSS=0020304"}]
    class BadDetail(FakeApi):
        def get_room_detail(self, danchi_id, room_id):
            if room_id == "0020304":
                raise RuntimeError("detail down")
            return DETAIL.get((danchi_id, room_id), {})
    stat = am.run(make_cfg(), BadDetail({"01": DANCHI}, {"20_2600": rooms2}, {}), p,
                  notify_fn=lambda *a, **k: True)
    assert stat["new"] == 1 and stat["pushed"] == 0
    s = am.load_snapshot(p)
    assert "0020304" not in s["rooms"]      # 失败不进快照 → 下次重试
    # 详情恢复后重试成功
    stat2 = am.run(make_cfg(), FakeApi({"01": DANCHI}, {"20_2600": rooms2},
                                        {**DETAIL, ("20_2600", "0020304"): {"year": "15", "floor": "2階 /5階",
                                                                             "facility": "エレベーター、リフォーム"}}), p,
                   notify_fn=lambda *a, **k: True)
    assert stat2["new"] == 1 and stat2["pushed"] == 1
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_actions_monitor.py -v`
Expected: 新增 3 个测试 FAIL —— `AttributeError: module 'actions_monitor' has no attribute 'run'`

- [ ] **Step 3: 实现**

在 `actions_monitor.py` **末尾追加**（Task 2 的辅助函数之后）：

```python
# ---- 采集当前全量在架房 ----

def current_rooms(api, cfg, cond, table, danchi_static):
    """遍历 areas 收集当前全量在架房; 首次见到的团地算通勤/电梯并入缓存。返回 {room_id: info}。"""
    rooms = {}
    for area in cfg.areas:
        for d in api.get_danchi_list(area, cond, cfg.wide_filter, "tokyo"):
            if int(d.get("roomCount") or 0) <= 0:
                continue
            did = d["id"]
            danchi = M.parse_danchi(d, "tokyo")
            if did not in danchi_static:
                commute = costtime.resolve_commute_min(danchi.station_name, api, table)
                elevator = "エレベーター" in (api.get_danchi_detail(did).get("facility") or "")
                danchi_static[did] = {"commute_min": commute, "has_elevator": elevator,
                                      "walk_min": danchi.walk_min, "name": d.get("name") or "",
                                      "skcs": d.get("skcs") or ""}
            st = danchi_static[did]
            danchi.commute_min = st["commute_min"]
            danchi.has_elevator = st["has_elevator"]
            for r in api.get_room_list(did, cond, cfg.wide_filter, "tokyo"):
                room = M.parse_room(r, danchi)
                rooms[room.room_id] = {"danchi_id": did, "name": room.name, "url": room.url,
                                       "rent": room.rent, "commonfee": room.commonfee,
                                       "madori": room.madori, "area": room.area, "floor": room.floor}
    return rooms


# ---- 上新: 富化+打分+通知 ----

def _build_room(rid, info, st):
    return M.Room(room_id=rid, danchi_id=info["danchi_id"], danchi_name=st["name"],
                  name=info["name"], url=info["url"], rent=info["rent"], commonfee=info["commonfee"],
                  madori=info["madori"], area=info["area"], floor=info["floor"], total_floors=0,
                  has_elevator=st["has_elevator"], renovated=False, walk_min=st["walk_min"],
                  commute_min=st["commute_min"], prefecture="tokyo", skcs=st["skcs"])


def run(cfg, api, snapshot_path=SNAPSHOT_PATH, notify_fn=None):
    if notify_fn is None:
        notify_fn = notify.notify_new_room
    webhook = getattr(getattr(cfg, "discord", None), "webhook_url", "")
    snapshot = load_snapshot(snapshot_path) or {}
    table = _load_table(api, cfg, snapshot)
    cond = build_cond(cfg.destination.station_cd, table,
                      cfg.destination.commute_max_min, cfg.destination.change_max)
    danchi_static = snapshot.get("danchi_static", {})
    rooms = current_rooms(api, cfg, cond, table, danchi_static)
    new_items = diff_new(rooms, snapshot.get("rooms", {}))
    pushed = 0
    failed = set()
    for rid, info in new_items.items():
        st = danchi_static.get(info["danchi_id"])
        if not st:
            failed.add(rid)
            continue
        try:
            room = _build_room(rid, info, st)
            detail = api.get_room_detail(info["danchi_id"], rid)
            M.enrich_room_from_detail(room, detail, cfg.precise.renovated_keywords)
            ok, score, reason = S.should_push(room, cfg)
        except Exception:
            failed.add(rid)   # 失败不进快照 → 下次运行重试
            continue
        if not ok:
            continue
        if notify_fn(webhook, room, score, reason):
            pushed += 1
    snapshot["danchi_static"] = danchi_static
    snapshot["rooms"] = {rid: info for rid, info in rooms.items() if rid not in failed}
    old = load_snapshot(snapshot_path)
    save_snapshot(snapshot_path, snapshot)
    return {"total": len(rooms), "new": len(new_items), "pushed": pushed,
            "changed": old != snapshot}


# ---- 提交快照(仅 Actions; 本地无 GITHUB_TOKEN 跳过) ----

def _commit_snapshot(path, room_count):
    if not os.environ.get("GITHUB_TOKEN"):
        return False
    branch = os.environ.get("GITHUB_REF_NAME") or "main"   # Actions 是 detached HEAD, 先回分支再 push
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"])
    subprocess.run(["git", "checkout", "-B", branch])
    subprocess.run(["git", "add", path])
    r = subprocess.run(["git", "commit", "-m", f"snapshot: {room_count} rooms"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False
    subprocess.run(["git", "push", "origin", branch])
    return True


def main():
    cfg = load_config("config.actions.yaml")
    api = UrApi(cfg.http.user_agent, cfg.http.timeout, cfg.http.retry_max, cfg.http.backoff_base_sec)
    stat = run(cfg, api)
    print(f"total={stat['total']} new={stat['new']} pushed={stat['pushed']} changed={stat['changed']}",
          flush=True)
    if stat["changed"]:
        _commit_snapshot(SNAPSHOT_PATH, stat["total"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_actions_monitor.py -v`
Expected: 全部 PASS（Task 2 的 6 个 + Task 3 的 3 个）

- [ ] **Step 5: 提交**

```bash
git add actions_monitor.py tests/test_actions_monitor.py
git commit -m "feat: actions_monitor 采集/编排/快照提交"
```

---

### Task 4: 工作流 + 全量验证 + README

**Files:**
- Create: `.github/workflows/monitor.yml`
- Modify: `README.md`
- Test: 全量 `tests/`

**Interfaces:** 无（部署 + 验证）

- [ ] **Step 1: 创建工作流**

创建 `.github/workflows/monitor.yml`：

```yaml
name: danchi-monitor

on:
  schedule:
    - cron: '25,30,35,40,45 1,3,5,7,9 * * *'   # UTC=JST 10:25-18:45 偶数小时密集(5分)
    - cron: '*/30 * * * *'                      # 全天 30 分钟兜底
  workflow_dispatch:                            # 手动触发(任意分支可测)

permissions:
  contents: write

concurrency:
  group: danchi-monitor
  cancel-in-progress: true

jobs:
  monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install requests pyyaml
      - name: 监控上新
        env:
          DANCHI_DISCORD_WEBHOOK: ${{ secrets.DANCHI_DISCORD_WEBHOOK }}
        run: python actions_monitor.py
```

- [ ] **Step 2: 冒烟验证脚本可导入、本地 dry-run 不炸**

Run:
```bash
.venv/bin/python -c "from actions_monitor import run, main; print('ok')"
```
Expected: `ok`（无 `GITHUB_TOKEN` 时 `main()` 也只打印 stat、不提交）

- [ ] **Step 3: 全量测试**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全部 PASS（既有测试 + 新增 actions 测试；既有 `test_llm_comment.py` 等不受影响）

- [ ] **Step 4: 更新 README**

`README.md` 的"运行"小节改为（替换原第 16 行及其上下的运行说明）：

```markdown
## 运行
- **线上（推荐）**：GitHub Actions（`.github/workflows/monitor.yml`）定时抓取 + 快照 diff 上新通知 Discord。仓库 PUBLIC → webhook 走 secret `DANCHI_DISCORD_WEBHOOK`；配置在 `config.actions.yaml`（无 webhook）；快照在 `snapshot/rooms.json`。
- 手动本地单次：`python run_monitor_once.py`（需本地 `config.yaml`）
- 旧常驻模式 `python main.py` 已停用（本地不再自启轮询）
```

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/monitor.yml README.md
git commit -m "feat: GitHub Actions 工作流 + README 更新"
```

- [ ] **Step 6: 推分支 + 手动触发验证**

Run:
```bash
git push -u origin gh-actions-monitor
```
然后在 GitHub 上 `Actions → danchi-monitor → Run workflow` 手动触发一次，确认：日志无异常、首次跑静默建基线（total>0、new=0）、`snapshot/rooms.json` 被提交。**先不合并 main**（等用户确认）。
