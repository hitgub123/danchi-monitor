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
- 手动单次监控：`python run_monitor_once.py`
- 常驻运行：`python main.py`（每月 discover + 日间5分/夜间30分轮询）
- 日志：控制台 + 可重定向到文件

## 数据
- `data.db`：目标团地 / 已见房间 / poll_log（轮询快照，可做时间序列分析）/ history

## 反封说明
轻量 JSON API + 固定 UA + 随机抖动 + 403/429 指数退避。若频繁被封，调大 `schedule` 间隔。
