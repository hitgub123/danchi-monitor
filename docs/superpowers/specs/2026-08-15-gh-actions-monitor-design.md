# GitHub Actions 实时上新监控 — 设计文档

- 日期：2026-08-15
- 分支：`gh-actions-monitor`（从 main 分叉）
- 状态：设计已与用户确认，待实现

---

## 1. 背景与动机

本地 tmux 常驻监控（`main.py`）依赖电脑开机，工作日白天常关机 → 漏房。切换到 GitHub Actions 云端执行：**公共仓库 Actions 分钟数无限**，且彻底解决"开机才监控"的问题。

**已执行的操作（本任务前置）：**
- 杀掉本地 tmux 会话 `danchi-monitor`
- 从 `C:\Users\81802\start-claude-bridge.cmd` 移除 `start-danchi-monitor.sh` 自启行（保留两行 discord 桥接）
- 本地从此不再自动轮询（`run_monitor_once.py` 仍可手动调试）

**数据观测结论（作为节奏设计依据）：**
- UR 无"更新时间"字段（API 与页面均无）
- 上新槽：JST 10:30/12:30/14:30/16:30/18:30（±3 分），**非每天每个槽都发**
- 偶有非槽位上架（如 8/11 有 13:53-14:06 的房源）

---

## 2. 目标与非目标

**目标：**
1. 云端实时检测上新 → 打分 → 推送 Discord
2. **快照-diff 结构性保证不漏**（不依赖轮询频率）
3. 尽量少打 UR API（GitHub 数据中心 IP 反封风险 > 家宽）
4. 公共仓库安全（webhook 走 secret，配置不含密钥）

**非目标：**
- 不做时间序列/规律分析，不保存轮询记录（用户明确要求）
- 不改动旧代码（`main.py`/`db.py`/`monitor.py`/`schedule.py` 保留但不再执行）
- 不删除旧分支内容

---

## 3. 关键事实约束（已核实）

| 约束 | 含义 |
|---|---|
| GitHub Actions `schedule` **最短 5 分钟** | 3 分钟不可行 |
| `schedule` 只在**默认分支（main）**生效 | 需合并回 main 后 cron 才跑；`workflow_dispatch` 任意分支可手动测 |
| 高负载时 scheduled 运行会**延迟/被丢弃** | 快照-diff 对延迟鲁棒（延迟=通知变晚，不漏） |
| 公共仓库 **60 天无活动自动禁用** scheduled | 快照 commit（房间变化时）维持活动 |
| 仓库 **PUBLIC** | webhook 必须存 Actions secret；提交的配置文件必须无密钥 |

---

## 4. 设计

### 4.1 分支与上线流程

```
gh-actions-monitor 分支开发 ──► workflow_dispatch 手动测试 ──► 合并回 main ──► cron 生效
```
合并前本地已停、Actions 未跑 → 有一段空窗（用户已接受）。

### 4.2 新增文件

| 文件 | 作用 |
|---|---|
| `.github/workflows/monitor.yml` | 工作流定义（schedule + workflow_dispatch） |
| `actions_monitor.py` | 入口脚本（唯一新增逻辑） |
| `config.actions.yaml` | 公共安全配置（webhook 留空，评分参数同本地 config.yaml，schedule 段可精简） |
| `snapshot/rooms.json` | 快照：上次扫描的全量在架房 + danchi 静态缓存 + station_condition |

### 4.3 工作流 `.github/workflows/monitor.yml`

```yaml
name: danchi-monitor

on:
  schedule:
    - cron: '25,30,35,40,45 1,3,5,7,9 * * *'   # UTC=JST 10:25-18:45 偶数小时密集(5分)
    - cron: '*/30 * * * *'                      # 全天 30 分钟兜底
  workflow_dispatch:

permissions:
  contents: write        # 让 GITHUB_TOKEN 能提交快照

concurrency:
  group: danchi-monitor
  cancel-in-progress: true   # 防重叠运行 push 冲突

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install requests pyyaml
      - name: 监控上新
        env:
          DANCHI_DISCORD_WEBHOOK: ${{ secrets.DANCHI_DISCORD_WEBHOOK }}
        run: python actions_monitor.py
```

**节奏效果**（一天约 68 次运行，公共仓库无限额）：
- 密集窗口（JST 10:25-18:45 每偶数小时 :25/:30/:35/:40/:45）→ 槽位附近上架 ≤5 分钟发现
- 全天 30 分钟兜底 → 非槽位上架 ≤30 分钟发现
- **不漏由 diff 结构保证**：只要房间在任一次成功运行仍上架，就会被下一次 diff 抓到；唯一会漏的是两次成功运行之间上架又被租走（<5 分钟窗口，UR 几乎不可能）

### 4.4 脚本逻辑 `actions_monitor.py`

复用：`ur_api.py`（API 客户端）、`models.py`（解析）、`costtime.py`（通勤）、`score.py`（打分）、`notify.py`（Discord）、`config.py`（配置加载）。

```
1. cfg = load_config("config.actions.yaml")；webhook 取自环境变量 DANCHI_DISCORD_WEBHOOK(secret)
2. 读 snapshot/rooms.json → {cond, danchi_static, rooms}
3. cond 存在则复用；否则下载 cost-time XML 构建并缓存（静态文件，只下首次/缺失时）
4. 遍历 cfg.areas → api.get_danchi_list(area, cond, wide, pref) → 每团地 get_room_list → 收集当前全量 room_id → {danchi_id, name, url}
   - 每团地首次见到：算 commute_min(costtime) + has_elevator(团地 detail)，写入 danchi_static 缓存
5. 若 snapshot 无 rooms（首跑/缺失）→ 静默写基线（当前全量），不通知
6. new_rooms = 当前 rooms − 快照 rooms
7. 每个新房间：get_room_detail → models.enrich_room_from_detail 富化 → score.should_push → 达标则 notify.notify_new_room(webhook, room, score, reason)
   - 详情/打分失败：跳过本次通知（房间下次运行会重试，因为没进快照）
8. 覆盖写 snapshot（当前全量 rooms + danchi_static + cond）
9. 快照与运行前不同才 git commit + push（github-actions[bot]，默认 GITHUB_TOKEN），否则跳过
```

**快照格式**（最新版覆盖，唯一持久状态）：
```json
{
  "cond": "2827,60,2",
  "danchi_static": { "20_2600": { "commute_min": 30, "has_elevator": true, "walk_min": 10 } },
  "rooms": { "001080409": { "danchi_id": "20_2600", "name": "72号棟409号室", "url": "/chintai/..." } }
}
```

### 4.5 复用与去重

| 模块 | 处理 |
|---|---|
| `ur_api.py` | ✅ 原样复用（含 403/429 指数退避 + 抖动） |
| `models.py` / `score.py` / `notify.py` / `config.py` | ✅ 原样复用 |
| `costtime.py` | ✅ 复用 `parse_cost_time`/`build_station_condition`/`resolve_commute_min` |
| `db.py` / `main.py` / `monitor.py` / `schedule.py` | ❌ 不用（Actions 无本地 DB/循环） |

---

## 5. 边界与错误处理

| 场景 | 行为 |
|---|---|
| 首跑（快照不存在） | 静默建基线，不通知（避免刷屏） |
| 房间上架后又消失（<两次运行间） | 会漏（结构性极限；5 分钟窗口下 UR 几乎不可能） |
| 房间消失又重现 | 会重新通知（可接受，少见） |
| 房间详情/打分失败 | 不通知、不进快照 → 下次运行重试 |
| 快照 commit/push 失败 | 打印错误；下次运行重新 checkout 后 diff 仍正确（self-heal） |
| 403/429 被 UR 限流 | 复用指数退避；若持续被 Ban → 降频（改 cron）或暂停 workflow |
| 并发运行重叠 | `concurrency` 取消旧运行，防 push 冲突 |
| 60 天无活动禁用 | 房间变化产生快照 commit 维持活动；若被禁用，编辑 cron 可复活 |

---

## 6. 测试

1. **单测**：`actions_monitor.py` 的 diff/基线逻辑用 fake ur_api（同 `tests/test_monitor.py` 的 FakeApi 模式）；新增 `tests/test_actions_monitor.py`
2. **复用模块测试**：现有 `tests/test_ur_api.py`/`test_score.py`/`test_models.py` 等不变，全量 pytest 通过
3. **手动实测**：推送分支后 `workflow_dispatch` 触发一次，观察日志 + 首次跑是否静默建基线；再触发一次确认 diff 与通知
4. **合并后验证**：cron 生效后观察数次运行

---

## 7. 部署与回滚

- **上线**：合并 `gh-actions-monitor` → main → cron 生效；在 GitHub Secrets 添加 `DANCHI_DISCORD_WEBHOOK`
- **回滚**：`git revert` 或 `workflow_disable` 暂停；旧代码未删，随时可回本地
- **本地调试**：`python run_monitor_once.py` 仍可用（需本地 config.yaml）
