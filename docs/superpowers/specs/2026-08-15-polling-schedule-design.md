# 轮询节奏批次化 + 停用 LLM 调用 — 设计文档

- 日期：2026-08-15
- 项目路径：`/home/cc/projects/danchi-monitor/`
- 状态：设计已与用户确认，待实现

---

## 1. 背景与动机

### 1.1 数据观测（2026-08-02 ~ 08-15, 基于 poll_log 19.6 万条快照）

对 8/2–8/15 的 `poll_log` 相邻快照 diff，区分"活跃监控期（轮询间隔 ≤12min）"与"停机堆积期"，得到：

- **UR 上新是白天批量刷新**：活跃期内 62 次真上新，出现窗口稳定落在 **10:30 / 12:30 / 14:30 / 16:30 / 18:30**（检测在 :35–:43，滞后约 6–10 分钟）。下架（remove）同样落在这 5 个槽。
- **槽不是每天都发全**：5 个候选槽每天随机打掉 1–2 个（如 8/9 缺 16:30）。12:30 与 18:30 最稳。
- **夜间（22:00–08:00）几乎零上新**：整夜连续监控的日子一单未抓。
- **假信号**：周五 23:00 的"大上新"是监控停机恢复后的堆积发现（间隔 21–47h），非实际上架时刻。
- **UR 侧没有更新时间字段**：`list_bukken` / `room/list` / `detail_bukken_bukken` / `detail_room` 四个 API 与公开 HTML 页均无"更新日/登録日"；`availableDate` 是"入居可能日"，不是上架时间。因此**无法从 UR 拿到真实上架时刻，只能靠检测时间**。

### 1.2 现状问题

- 当前 `main.py` 是**无相位锁定的固定间隔轮询**：白天每 5 分钟、夜间每 30 分钟 `time.sleep(interval + uniform(0,5))`，轮询时刻自由漂移，抓 :30 批次的延迟可达 0–10 分钟且不稳定。
- 夜间 30 分钟轮询纯属浪费：没新房、白白增加被 UR 反封（403/429）的风险。
- 日/夜分界（8–22）写死在 `main.py::pick_interval`。
- `llm_comment` 虽已被 config 开关 + 环境变量双层门控（当前实际不调用），但死代码留在调用路径里，有被误开风险。**用户决定：注释掉调用、保留代码**，将来可恢复。

---

## 2. 目标与非目标

**目标：**
1. 相位锁定到 UR 的上新批次：**白天基准 10 分钟**，在 **偶数小时 :27–:42 密集窗口内加密到 3 分钟一格**，使 :30 批次上新的房 0–3 分钟内被发现。
2. **夜间降到 60 分钟**心跳（保留监控存活探测 + 兜底，不彻底停）。
3. 日/夜分界从写死改为配置化。
4. **注释掉 LLM 调用**（不删除代码）。

**非目标：**
- 不修"监控可靠性"（WSL 睡眠 / tmux 中断导致的大面积停机）——另立任务，但本次改动后仍受其影响。
- 不做智能补抓（如检测到新批次后临时加密）——本次只做固定的时间段节奏，YAGNI。
- 不停止夜间轮询——按用户选择保留 60 分钟心跳。

---

## 3. 设计

### 3.1 调度核心：新增 `schedule.py`

纯逻辑、无 IO、可单测。核心是一个纯函数：给定当前时刻，返回**未来最近的网格轮询点**。

```
PollSchedule
├── day_interval_min: int        # 白天基准（10）
├── night_interval_min: int      # 夜间（60）
├── day_start_hour / day_end_hour: int   # 白天区间（8/22）
├── dense: Optional[DenseWindows]
└── next_poll_at(now: datetime) -> datetime
```

`DenseWindows`：
```
DenseWindows
├── hours: List[int]        # [10, 12, 14, 16, 18]
├── start_min: int          # 27
├── end_min: int            # 42
└── interval_min: int       # 3
```

**网格判定**（对任意分钟 `m`）：

| 条件 | 网格点 |
|---|---|
| `m.hour ∈ dense.hours` 且 `start_min ≤ m.minute ≤ end_min` | `(m.minute - start_min) % interval_min == 0` → `27,30,33,36,39,42` |
| 白天（`day_start_hour ≤ m.hour < day_end_hour`） | `m.minute % day_interval_min == 0` → `:00,:10,:20,:30,:40,:50` |
| 夜间 | `m.minute % night_interval_min == 0` → `:00` |

**`next_poll_at(now)`**：从 `now+1min` 起逐分钟扫描，返回第一个网格点；无则继续到次日。`:00` 整点恒为网格点，**永不落空**。天然处理：密集窗口与 10 分钟网格在 `:30/:40` 重合（去重由"返回下一个"保证）、昼夜分界、午夜翻日。

**为什么用"下一网格点"而非"每 N 分钟 sleep"**：后者的相位会因轮询耗时与抖动累积漂移；前者每次从真实墙钟重新计算，**零累积漂移**。

### 3.2 配置：`config.py` + `config.yaml`

`config.py` 新增两个 dataclass（`Schedule` 扩展 + 新增 `DenseWindows`）：

```python
@dataclass
class DenseWindows:
    hours: List[int]
    start_min: int
    end_min: int
    interval_min: int

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

- `_section()` 已按 dataclass 字段过滤，旧配置缺字段走默认值，向后兼容。
- `dense_windows` 从 yaml 的 dict 构造（缺省为 `None` = 无密集窗口，退化为纯 10/60 节奏）。

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

### 3.3 主循环：`main.py`

- **删除** `pick_interval()`（8–22 分界进 config）。
- 循环尾部从 `sleep(固定间隔 + 抖动)` 改为：

```python
sched = PollSchedule(
    day_interval_min=cfg.schedule.day_interval_min,
    night_interval_min=cfg.schedule.night_interval_min,
    day_start_hour=cfg.schedule.day_start_hour,
    day_end_hour=cfg.schedule.day_end_hour,
    dense=cfg.schedule.dense_windows,
)
# 循环内：
target = sched.next_poll_at(datetime.now())
sleep_sec = max(0.0, (target - datetime.now()).total_seconds())
time.sleep(sleep_sec + random.uniform(0, cfg.schedule.max_jitter_sec))
```

- 抖动 `uniform(0, max_jitter_sec)` 保持反封；因下一轮从真实墙钟重算，抖动不累积漂移。
- `_loop` 内的 prune / discover / stats 检查保持不动（10 分钟粒度检查足够）。

### 3.4 LLM 调用：`monitor.py`

**注释掉** `run_monitor()` 内的 LLM 点评调用块（`monitor.py:94-97`）：

```python
# 注释掉 LLM 点评调用（2026-08-15, 用户决策: 停用但保留代码）
# comment = comment_fn(room) if webhook and getattr(...) else ""
# if comment:
#     import notify
#     notify.notify_llm_comment(webhook, room, comment)
```

保留：`llm_comment.py`、`notify.notify_llm_comment`、`config.discord.llm_comment` 字段、相关测试。`comment_fn` 仍作为 `run_monitor` 形参保留（签名不变，测试兼容），仅调用点被注释。恢复只需取消注释。

### 3.5 数据流（改动后单次循环）

```
next_poll_at(now) ──► sleep 到网格点 + 抖动
        │
        ▼
run_monitor()          # 逐团地: list → room_list → 新房间 detail+评分 → push → log_poll
        │
        ▼
loop 顶部: prune/discover/stats 检查（周期不变）
```

---

## 4. 边界与错误处理

| 场景 | 行为 |
|---|---|
| `next_poll_at` 无下一网格点（理论不发生） | 返回 `now + max(night_interval, day_interval)` 兜底 |
| `now` 恰在网格点上 | 返回严格之后的网格点，避免忙循环 |
| 密集窗口与 10 分钟网格重合（`:30/:40`） | 返回下一个，天然去重 |
| 昼夜分界（`07:59` / `21:59`） | 按目标分钟所属区段判定网格 |
| 午夜翻日（`23:59`） | 返回次日 `:00`，datetime 带日期 |
| config 缺 `dense_windows` | `None` → 退化为纯 10/60 节奏 |
| 反封 403/429 | 走既有 `UrApi` 指数退避，不在此次改动范围 |

---

## 5. 测试

**新增 `tests/test_schedule.py`：**
- 密集窗口内网格点正确（`27/30/33/36/39/42`），窗口外回到 10 分钟网格
- `next_poll_at` 严格返回未来（恰在网格点 → 下一格）
- 昼夜分界、午夜翻日、`dense=None` 退化、`:00` 兜底
- 参数化小时循环覆盖 5 个密集小时

**更新：**
- `tests/test_config.py`：解析新的 `schedule`（含 `dense_windows`）与缺省字段向后兼容
- `tests/test_main.py`：`pick_interval` 已删除 → 移除/改写相关断言
- `tests/test_monitor.py`：确认 LLM 调用被注释后，`run_monitor` 不调 `comment_fn`（`comment_fn` 传 stub 验证不被调用）
- `tests/test_notify.py`：保留（`notify_llm_comment` 未被删除，测试不动）

**全量 `pytest` 通过** 是完成定义。

---

## 6. 部署与回滚

- 常驻进程为 tmux + Windows 开机自启（见 `~/projects/danchi-monitor` 既有部署）。改后重启进程即可。
- `schedule.py` / `next_poll_at` 是纯函数，回归风险集中在 `main.py` 循环尾部，约 10 行。
- 回滚：`git revert` 即可；旧节奏（5/30 无锁定）靠 git 历史保留。
- **注意**：本次不解决"监控可靠性"（WSL 睡眠 / tmux 中断停机）。停机期间新旧节奏都无法抓到房，且会再次制造"假上新高峰"——建议下一步单独处理（防睡眠 / watchdog / 迁云）。
