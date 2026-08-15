# 轮询节奏批次化 + 停用 LLM 调用 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把常驻监控从"无相位锁定的固定间隔轮询"改为"相位锁定到 UR 上新批次"（白天 10 分钟基准 + 偶数小时 :27–:42 密集窗口 3 分钟一格 + 夜间 60 分钟心跳），并注释停用 LLM 点评调用（保留代码）。

**Architecture:** 新增纯逻辑模块 `schedule.py`（`PollSchedule.next_poll_at(now)` 返回未来最近的网格轮询时刻，纯函数可单测）。`main.py` 删掉 `pick_interval`，循环尾部改为"睡到下一网格点 + 抖动"。节奏参数全部进 `config.yaml`/`config.py`。`monitor.py` 的 LLM 调用块注释掉。

**Tech Stack:** Python 3.14（stdlib: `dataclasses`/`datetime`/`sqlite3`/`urllib`），pytest 9.1.1（venv）。

## Global Constraints

- 节奏：白天 `day_interval_min=10`，夜间 `night_interval_min=60`，昼夜分界 `day_start_hour=8`/`day_end_hour=22`。
- 密集窗口：`hours=[10,12,14,16,18]`、`start_min=27`、`end_min=42`、`interval_min=3` → 网格 `27,30,33,36,39,42`。
- 反封抖动 `max_jitter_sec=5`。
- **LLM 只注释、不删除**：保留 `llm_comment.py`、`notify.notify_llm_comment`、`config.discord.llm_comment` 字段、`db.history.llm_comment` 列、`tests/test_llm_comment.py`。
- **工作区有用户未提交改动**：`config.py`（webhook 环境变量支持）、`show_current.py`（新文件）。每个 commit 步骤**只 `git add` 任务涉及的具体文件，严禁 `git add -A` / `git add .`**，且绝不 `git checkout`/`git stash` 这些文件。
- 每任务 TDD：先写失败测试 → 跑确认失败 → 最小实现 → 跑确认通过 → 提交。
- 测试命令统一用 `.venv/bin/python -m pytest <path>`。

---

### Task 1: 新增 `schedule.py`（相位锁定调度核心）

**Files:**
- Create: `schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produces: `schedule.DenseWindows(hours, start_min, end_min, interval_min)`；`schedule.PollSchedule(day_interval_min, night_interval_min, day_start_hour=8, day_end_hour=22, dense=None).next_poll_at(now: datetime) -> datetime`。Task 2 从本模块 `import DenseWindows`，Task 3 用 `PollSchedule`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_schedule.py`：

```python
from datetime import datetime
import pytest
from schedule import PollSchedule, DenseWindows

DENSE = DenseWindows(hours=[10, 12, 14, 16, 18], start_min=27, end_min=42, interval_min=3)

def make_sched(dense=DENSE):
    return PollSchedule(day_interval_min=10, night_interval_min=60,
                        day_start_hour=8, day_end_hour=22, dense=dense)

def dt(hh, mm, day=15):
    return datetime(2026, 8, day, hh, mm)

@pytest.mark.parametrize("minute", [27, 30, 33, 36, 39, 42])
def test_dense_grid_points_are_next_targets(minute):
    # 窗口内网格点前一刻 → 下一网格就是该分钟
    assert make_sched().next_poll_at(dt(12, minute - 1)) == dt(12, minute)

def test_dense_window_first_grid():
    assert make_sched().next_poll_at(dt(12, 20)) == dt(12, 27)

def test_non_grid_minute_in_window_advances():
    assert make_sched().next_poll_at(dt(12, 28)) == dt(12, 30)

def test_after_dense_window_returns_to_day_grid():
    # 12:45 已出密集窗口(>42), 回落 10 分钟日网格
    assert make_sched().next_poll_at(dt(12, 45)) == dt(12, 50)

def test_non_dense_hour_uses_day_grid():
    assert make_sched().next_poll_at(dt(13, 5)) == dt(13, 10)

def test_night_grid_every_60_min():
    assert make_sched().next_poll_at(dt(23, 10)) == dt(0, 0, day=16)

def test_strictly_after_when_exactly_on_grid():
    assert make_sched().next_poll_at(dt(12, 30)) == dt(12, 33)

def test_day_night_boundary_morning():
    assert make_sched().next_poll_at(dt(7, 59)) == dt(8, 0)

def test_day_night_boundary_evening():
    assert make_sched().next_poll_at(dt(21, 59)) == dt(22, 0)

def test_midnight_rollover():
    assert make_sched().next_poll_at(dt(23, 59)) == dt(0, 0, day=16)

def test_no_dense_degradation():
    s = make_sched(dense=None)
    assert s.next_poll_at(dt(12, 27)) == dt(12, 30)
    assert s.next_poll_at(dt(12, 30)) == dt(12, 40)
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_schedule.py -v`
Expected: `ModuleNotFoundError: No module named 'schedule'`（或 import 错误）

- [ ] **Step 3: 最小实现**

创建 `schedule.py`：

```python
# schedule.py — 相位锁定的轮询调度
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

@dataclass
class DenseWindows:
    hours: List[int]
    start_min: int
    end_min: int
    interval_min: int

class PollSchedule:
    """按一天中的时间段返回"下一网格轮询时刻"。纯逻辑、无 IO、可单测。

    网格判定（对任意分钟 t）：
    - 密集窗口内:   (t.minute - start_min) % interval_min == 0
    - 白天:         t.minute % day_interval_min == 0
    - 夜间:         t.minute % night_interval_min == 0
    每个整点 :00 恒为网格点（0 % 任何数 == 0），故 next_poll_at 永不落空。
    """

    def __init__(self, day_interval_min, night_interval_min,
                 day_start_hour=8, day_end_hour=22, dense=None):
        self.day_interval_min = day_interval_min
        self.night_interval_min = night_interval_min
        self.day_start_hour = day_start_hour
        self.day_end_hour = day_end_hour
        self.dense = dense

    def _is_grid(self, t: datetime) -> bool:
        d = self.dense
        if d and t.hour in d.hours and d.start_min <= t.minute <= d.end_min:
            return (t.minute - d.start_min) % d.interval_min == 0
        if self.day_start_hour <= t.hour < self.day_end_hour:
            return t.minute % self.day_interval_min == 0
        return t.minute % self.night_interval_min == 0

    def next_poll_at(self, now: datetime) -> datetime:
        t = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(24 * 60 * 2):  # :00 恒为网格点, 60 分钟内必收敛; 上限仅兜底
            if self._is_grid(t):
                return t
            t += timedelta(minutes=1)
        return now + timedelta(minutes=max(self.night_interval_min, self.day_interval_min))
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_schedule.py -v`
Expected: 11 个测试全 PASS

- [ ] **Step 5: 提交**

```bash
git add schedule.py tests/test_schedule.py
git commit -m "feat: 新增 schedule.py 相位锁定调度(PollSchedule.next_poll_at)"
```

---

### Task 2: 配置化节奏（`config.py` + `config.yaml`）

**Files:**
- Modify: `config.py`（Schedule dataclass、load_config 转换 dense_windows、import DenseWindows）
- Modify: `config.yaml`（schedule 段）
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `schedule.DenseWindows`（Task 1）
- Produces: `config.Schedule` 新增字段 `day_start_hour/day_end_hour/dense_windows/max_jitter_sec`；`cfg.schedule.dense_windows` 是 `DenseWindows` 实例（Task 3 直接传入 `PollSchedule`）。
- 注意：`config.py` 工作区含用户未提交的 webhook 环境变量改动，编辑基于**工作区现状**叠加，不还原它。

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 追加：

```python
def test_schedule_config_parsed():
    cfg = load_config("config.yaml")
    assert cfg.schedule.day_interval_min == 10
    assert cfg.schedule.night_interval_min == 60
    assert cfg.schedule.day_start_hour == 8
    assert cfg.schedule.day_end_hour == 22
    assert cfg.schedule.max_jitter_sec == 5
    assert cfg.schedule.dense_windows is not None
    assert cfg.schedule.dense_windows.hours == [10, 12, 14, 16, 18]
    assert cfg.schedule.dense_windows.start_min == 27
    assert cfg.schedule.dense_windows.end_min == 42
    assert cfg.schedule.dense_windows.interval_min == 3
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_schedule_config_parsed -v`
Expected: FAIL —— `dense_windows` 为 `None`（yaml 尚未给该字段，或类型不符）

- [ ] **Step 3: 实现**

`config.py` 改动（3 处）：

顶部加 import（在 `import yaml` 之后）：
```python
from schedule import DenseWindows
```

`Schedule` dataclass 替换为：
```python
@dataclass
class Schedule:
    day_interval_min: int
    night_interval_min: int
    day_start_hour: int = 8
    day_end_hour: int = 22
    dense_windows: Optional[DenseWindows] = None
    poll_log_keep_days: int = 90
    max_jitter_sec: int = 5
```

`load_config` 里，`schedule=_section(Schedule, d["schedule"])` 改为（把 yaml 的 dict 转成 `DenseWindows`，再交给 `_section`）：
```python
    schedule_data = dict(d.get("schedule") or {})
    dw = schedule_data.get("dense_windows")
    if dw:
        schedule_data["dense_windows"] = DenseWindows(**dw)
    ...
    schedule=_section(Schedule, schedule_data),
```

`config.yaml` 的 `schedule` 段改为：
```yaml
schedule:
  day_interval_min: 10
  night_interval_min: 60
  day_start_hour: 8
  day_end_hour: 22
  dense_windows:
    hours: [10, 12, 14, 16, 18]
    start_min: 27
    end_min: 42
    interval_min: 3
  poll_log_keep_days: 90
  max_jitter_sec: 5
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: 3 个测试全 PASS（原 2 个 + 新 1 个）

- [ ] **Step 5: 提交**

```bash
git add config.py config.yaml tests/test_config.py
git commit -m "feat: 节奏参数配置化(白天10/夜间60/密集窗口), 昼夜分界移出代码"
```

---

### Task 3: `main.py` 主循环改用相位锁定调度

**Files:**
- Modify: `main.py`（删 `pick_interval`；加 `build_schedule`/`_next_sleep_sec`；循环尾部改睡到网格点）
- Test: `tests/test_main.py`（删 pick_interval 测试；`_fake_cfg` 补全字段；改写 wiring 测试；加 `_next_sleep_sec` 测试）

**Interfaces:**
- Consumes: `schedule.PollSchedule`（Task 1）、`cfg.schedule`（Task 2，含 `dense_windows`/`max_jitter_sec`）
- Produces: `main.build_schedule(s) -> PollSchedule`、`main._next_sleep_sec(sched, now, max_jitter_sec) -> float`

- [ ] **Step 1: 改写测试**

`tests/test_main.py` 全文替换为：

```python
import logging

import pytest
import main
from datetime import datetime


def _fake_cfg():
    # 与真实 Config 形状一致，schedule 含新字段
    class S:
        day_interval_min = 10
        night_interval_min = 60
        day_start_hour = 8
        day_end_hour = 22
        dense_windows = None
        poll_log_keep_days = 90
        max_jitter_sec = 5
    return type("C", (), {"schedule": S()})()


class _FakeDB:
    """_loop 需要的最小 DB 接口：count_danchi / get_meta / set_meta / prune_poll_log。"""
    def count_danchi(self):
        return 1  # 非空库，跳过启动时 bootstrap
    def get_meta(self, key):
        if key == "last_stats":
            return "9999999999"  # 未来时间戳 → 月度统计不触发(假DB无 conn)
        return None  # last_discover 未记录 → 触发一次 discover
    def set_meta(self, key, value):
        pass
    def prune_poll_log(self, keep_days):
        return 0


def test_next_sleep_sec_returns_seconds_until_target(monkeypatch):
    class FakeSched:
        def next_poll_at(self, now):
            return datetime(2026, 8, 15, 12, 30)
    monkeypatch.setattr("random.uniform", lambda a, b: 2.5)
    now = datetime(2026, 8, 15, 12, 20)
    # 12:30 - 12:20 = 600s + jitter 2.5
    assert main._next_sleep_sec(FakeSched(), now, 5) == 600 + 2.5


def test_loop_wiring_builds_schedule_and_sleeps(monkeypatch):
    """回归测试：_loop 必须用 cfg.schedule 构造调度器并睡到下一网格点，否则首轮后崩溃"""
    called = {}
    cfg = _fake_cfg()
    db = _FakeDB()
    api = object()

    def fake_discover(cfg_, api_, db_):
        return 0

    def fake_monitor(cfg_, api_, db_):
        return {}

    def fake_sleep(sec):
        called["sec"] = sec
        raise RuntimeError("stop_loop")

    monkeypatch.setattr("discover.run_discover", fake_discover)
    monkeypatch.setattr("monitor.run_monitor", fake_monitor)
    monkeypatch.setattr("main.time.sleep", fake_sleep)
    monkeypatch.setattr("main._next_sleep_sec", lambda sched, now, jitter: 42)

    with pytest.raises(RuntimeError, match="stop_loop"):
        main._loop(cfg, db, api)
    assert called["sec"] == 42  # 走到了 sleep，且用的是调度器算出的秒数


def test_loop_logs_monitor_error_count(monkeypatch, caplog):
    cfg = _fake_cfg()

    def fake_discover(cfg_, api_, db_):
        return 0

    def fake_monitor(cfg_, api_, db_):
        return {"danchi_checked": 3, "new_rooms": 1, "pushed": 0, "errors": ["boom"]}

    def fake_sleep(sec):
        raise RuntimeError("stop_loop")

    monkeypatch.setattr("discover.run_discover", fake_discover)
    monkeypatch.setattr("monitor.run_monitor", fake_monitor)
    monkeypatch.setattr("main.time.sleep", fake_sleep)
    monkeypatch.setattr("main._next_sleep_sec", lambda sched, now, jitter: 42)

    with caplog.at_level(logging.INFO, logger="main"):
        with pytest.raises(RuntimeError, match="stop_loop"):
            main._loop(cfg, _FakeDB(), object())
    msgs = [r.message for r in caplog.records if r.name == "main"]
    assert any("errors=1" in m for m in msgs)
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: FAIL —— `main.pick_interval`/`main.build_schedule`/`main._next_sleep_sec` 尚不存在（AttributeError / ModuleNotFoundError）

- [ ] **Step 3: 实现**

`main.py` 改动：

顶部 import 加一行：
```python
from datetime import datetime
```
并在 `from db import DB` 后加：
```python
from schedule import PollSchedule
```

**删除** `pick_interval` 函数（原 14-17 行）。

在 `_api` 之后、`_loop` 之前加两个函数：
```python
def build_schedule(s):
    return PollSchedule(
        day_interval_min=s.day_interval_min,
        night_interval_min=s.night_interval_min,
        day_start_hour=s.day_start_hour,
        day_end_hour=s.day_end_hour,
        dense=s.dense_windows,
    )

def _next_sleep_sec(sched, now, max_jitter_sec):
    """睡到 sched 的下一网格时刻 + 反封抖动。抖动不累积漂移（下轮从真实墙钟重算）。"""
    target = sched.next_poll_at(now)
    sec = max(0.0, (target - now).total_seconds())
    return sec + random.uniform(0, max_jitter_sec)
```

`_loop` 里，`while True:` 之前加一行：
```python
    sched = build_schedule(cfg.schedule)
```

循环尾部两行（原 `interval = pick_interval(...)` + `time.sleep(...)`）替换为一行：
```python
        time.sleep(_next_sleep_sec(sched, datetime.now(), cfg.schedule.max_jitter_sec))
```

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_main.py -v`
Expected: 3 个测试全 PASS

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_main.py
git commit -m "feat: main 主循环相位锁定到下一网格轮询点, 移除 pick_interval"
```

---

### Task 4: `monitor.py` 注释停用 LLM 调用

**Files:**
- Modify: `monitor.py`（注释掉 94-97 行 LLM 点评调用块）
- Test: `tests/test_monitor.py`（新增 1 个用例）

**Interfaces:**
- Consumes: 无新接口。`run_monitor(cfg, api, db, notify_fn=None, comment_fn=None)` 签名**不变**。
- Produces: 无。`comment_fn` 形参保留但调用点被注释，永不调用。

- [ ] **Step 1: 写失败测试**

在 `tests/test_monitor.py` 末尾追加：

```python
# ---- LLM 点评调用已注释停用（保留代码, 不调用）----

def test_llm_comment_call_disabled():
    cfg = make_cfg()
    class D: webhook_url = "https://discord.test/hook"; llm_comment = True
    cfg.discord = D()
    api = FakeApi({"01":DANCHI}, {"20_2600":ROOMS}, DETAIL)
    db = DB(":memory:"); db.init()
    def never_comment(room, **kw):
        raise AssertionError("comment_fn 不应被调用（LLM 调用已停用）")
    stat = monitor.run_monitor(cfg, api, db,
                               notify_fn=lambda url, room, score, reason: True,
                               comment_fn=never_comment)
    assert stat["pushed"] == 1
```

- [ ] **Step 2: 跑确认失败**

Run: `.venv/bin/python -m pytest tests/test_monitor.py::test_llm_comment_call_disabled -v`
Expected: FAIL —— `AssertionError: comment_fn 不应被调用`（webhook 有值 + llm_comment=True，当前会调用）

- [ ] **Step 3: 实现**

`monitor.py` 第 94-97 行替换为注释（**代码保留**，只注释调用）：

```python
                    # LLM 点评调用已停用（2026-08-15，用户决策：注释掉、保留代码，将来可恢复）
                    # comment = comment_fn(room) if webhook and getattr(getattr(cfg, "discord", None), "llm_comment", True) else ""
                    # if comment:
                    #     import notify
                    #     notify.notify_llm_comment(webhook, room, comment)
```

`run_monitor` 里 `if comment_fn is None: import llm_comment; comment_fn = llm_comment.llm_comment`（31-33 行）**保留不动**；`webhook` 变量仍被 `notify_fn(webhook, ...)` 使用，无未用变量问题。

- [ ] **Step 4: 跑确认通过**

Run: `.venv/bin/python -m pytest tests/test_monitor.py -v`
Expected: 全部 PASS（原 8 个 + 新 1 个）

- [ ] **Step 5: 提交**

```bash
git add monitor.py tests/test_monitor.py
git commit -m "chore: 注释停用 LLM 点评调用(保留代码, 2026-08-15)"
```

---

### Task 5: 全量验证 + 文档同步

**Files:**
- Modify: `README.md`（同步轮询节奏描述）
- Test: 全量 `tests/`

**Interfaces:** 无（验证 + 文档）

- [ ] **Step 1: 全量跑测试**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全部 PASS。`test_llm_comment.py` 仍通过（代码保留未删）。

- [ ] **Step 2: 冒烟验证导入与调度**

Run:
```bash
.venv/bin/python -c "
from config import load_config
from main import build_schedule
from schedule import PollSchedule
from datetime import datetime
cfg = load_config('config.yaml')
s = build_schedule(cfg.schedule)
print('next:', s.next_poll_at(datetime(2026,8,15,12,20)))
print('dense:', cfg.schedule.dense_windows)
"
```
Expected: `next: 2026-08-15 12:27:00`；`dense` 打印出 `DenseWindows(hours=[10, 12, 14, 16, 18], start_min=27, end_min=42, interval_min=3)`

- [ ] **Step 3: 同步 README**

`README.md` 第 16 行改为：
```markdown
- 常驻运行：`python main.py`（每月 discover + 日间10分/密集窗口3分/夜间60分轮询）
```

- [ ] **Step 4: 确认工作区状态干净（只含用户自己的改动）**

Run: `git status --short`
Expected: 只剩用户原有未提交项（` M config.py`、`?? show_current.py`），以及本计划 4 次提交之后的干净状态；无 `main.py`/`monitor.py`/`config.yaml`/`schedule.py` 残留 diff。

- [ ] **Step 5: 提交 README**

```bash
git add README.md
git commit -m "docs: README 同步新轮询节奏"
```
