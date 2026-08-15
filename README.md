# UR団地 新空房监控

监控东京圈 UR賃貸住宅中「到浜松町≤60分」的团地，发现新空房即打分推送 Discord，并记录每次轮询快照。

## 安装
```bash
pip install -r requirements.txt
```

## 配置
1. `config.yaml` → `discord.webhook_url` 填入你的 Discord webhook URL
2. 调整 `precise`（精确条件）、`weights`（打分权重）、`schedule`（轮询节奏）

## 运行
- **线上（推荐）**：GitHub Actions（`.github/workflows/monitor.yml`）定时抓取 + 快照 diff 上新通知 Discord。仓库 PUBLIC → webhook 走 secret `DANCHI_DISCORD_WEBHOOK`；配置在 `config.actions.yaml`（无 webhook）；快照在 `snapshot/rooms.json`。
- 手动本地单次：`python run_monitor_once.py`（需本地 `config.yaml`）
- 旧常驻模式 `python main.py` 已停用（本地不再自启轮询）

## 数据
- `data.db`：目标团地 / 已见房间 / poll_log（轮询快照，可做时间序列分析）/ history

## 反封说明
轻量 JSON API + 固定 UA + 随机抖动 + 403/429 指数退避。若频繁被封，调大 `schedule` 间隔。
