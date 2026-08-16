---
name: gcp-monitor
description: Use when deploying, redeploying, or troubleshooting the danchi-monitor GCP serverless monitor (Cloud Scheduler + Cloud Functions + Firestore + Secret Manager), or answering questions about its architecture, GCP free-tier quotas, the Discord webhook secret, or why the GitHub Actions schedule was replaced.
---

# GCP 监控（Cloud Scheduler + Cloud Functions）

## Overview

UR 上新监控跑在 GCP serverless：Cloud Scheduler（可靠分钟级 cron）→ HTTP+OIDC → Cloud Functions 2nd gen（Python 3.12，东京）→ 采集打分 → Discord + Firestore 快照。替代了 GitHub Actions 不可靠的 `schedule`（实测密集任务被丢弃、迟到 20-30 分钟）。

**核心文件**：`gcp_monitor.py`（函数入口）、`actions_monitor.py`（复用逻辑，`run()` 支持 `store=` 注入）、`deploy.sh`（构建+部署）。

## 架构（免费档：Scheduler 3 job / Functions 200万次 / Firestore 1GiB / Secret 6 个）

```
Cloud Scheduler (Asia/Tokyo 时区, OIDC 认证)
  monitor-dense  25,30,35,40,45 10,12,14,16,18 * * *   # 上新槽密集 5 分
  monitor-hourly 7 8,9,11,13,15,17,19,20 * * *          # 每小时兜底(夜间不跑)
      │ HTTP POST + OIDC token
      ▼
Cloud Functions gen2 (asia-northeast1, 入口 monitor)
  gcp_monitor.py: load_config(config.actions.yaml) → 注入 webhook(Secret Manager) → am.run(store=FirestoreStore)
      ▼
Firestore doc monitor/snapshot (房间快照)   +   Discord webhook
```

## 重新部署（改代码后）

```bash
.venv/bin/python -m pytest tests/test_gcp_monitor.py tests/test_actions_monitor.py   # 本地先测
.venv/bin/python -c "import gcp_monitor"                                              # 确认入口可导入
GCP_PROJECT=danchi-monitor ./deploy.sh     # 构建 .build/function + gcloud functions deploy
```
手动触发验证：`gcloud functions call monitor --region=asia-northeast1 --data '{}'`
期望第 1 次 `changed=true`（建基线），第 2 次 `changed=false`。

## 一次性初始设置（新项目/重做）

```bash
gcloud auth login
gcloud projects create danchi-monitor && gcloud config set project danchi-monitor
# console 开通计费(绑卡, 免费档不扣) → gcloud billing projects link
gcloud services enable firestore cloudfunctions cloudscheduler secretmanager \
  run cloudbuild artifactregistry billingbudgets
gcloud firestore databases create --location=asia-northeast1
gcloud secrets create danchi-discord-webhook --replication-policy=user-managed --locations=asia-northeast1
python -c "import yaml,sys; sys.stdout.write(yaml.safe_load(open('config.yaml'))['discord']['webhook_url'])" \
  | gcloud secrets versions add danchi-discord-webhook --data-file=-
SA="danchi-monitor@appspot.gserviceaccount.com"; FUNC=$(gcloud functions describe monitor --region=asia-northeast1 --format='value(url)')
gcloud functions add-iam-policy-binding monitor --region=asia-northeast1 --member="serviceAccount:$SA" --role=roles/cloudfunctions.invoker
gcloud secrets add-iam-policy-binding danchi-discord-webhook --member="serviceAccount:993032199178-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor
gcloud scheduler jobs create http monitor-dense  --location=asia-northeast1 --schedule="25,30,35,40,45 10,12,14,16,18 * * *" --time-zone=Asia/Tokyo --uri="$FUNC" --http-method=POST --oidc-service-account-email="$SA" --oidc-token-audience="$FUNC"
gcloud scheduler jobs create http monitor-hourly --location=asia-northeast1 --schedule="7 8,9,11,13,15,17,19,20 * * *" --time-zone=Asia/Tokyo --uri="$FUNC" --http-method=POST --oidc-service-account-email="$SA" --oidc-token-audience="$FUNC"
BA=$(gcloud billing accounts list --format='value(ACCOUNT_ID)' | head -1)
gcloud billing budgets create --billing-account="$BA" --display-name=monitor --budget-amount=1.00 --filter-projects=projects/danchi-monitor --threshold-rule=percent=0.5
```

## 部署的坑（都是实测踩过的，改代码前先看）

1. **gen2 Python 入口必须是 `main.py`** —— `--entry-point=monitor` 指的是 `main.py` 里的 `monitor` 函数。`gcp_monitor.py` 在仓库根供测试，deploy.sh 构建时 `mv gcp_monitor.py main.py`。
2. **`config.py` 顶部 `from schedule import DenseWindows`** —— deploy.sh 必须把 `schedule.py` 也复制进构建目录，否则函数启动 ImportError。
3. **函数环境没有 `GOOGLE_CLOUD_PROJECT` 环境变量**，且 `SecretManagerServiceClient` 没有 `.project` 属性 → `_webhook()` 用 `google.auth.default()` 解析项目（不要用 `os.environ['GOOGLE_CLOUD_PROJECT']` 或 `client.project`）。
4. **gen2 依赖额外 API**：`run`、`cloudbuild`、`artifactregistry` 必须启用；`billingbudgets` 建 Budget 也要。部署报 PERMISSION_DENIED 就先查这些 API。
5. **函数运行时 SA 要读 secret**：默认 compute SA（`<project-number>-compute@developer.gserviceaccount.com`）需 `roles/secretmanager.secretAccessor`，否则 `_webhook` 500。
6. **scheduler job 必须带 `--location`**（asia-northeast1）；cron 支持 `--time-zone=Asia/Tokyo`（GitHub Actions 做不到，这是迁移的核心优势）。
7. **快照存 Firestore 而不是 GCS**：GCS 免费档限美国三区，Firestore 免费档（1GiB + 5万读/2万写每天）不限区域——东京可用还免费。
8. **函数要 `--no-allow-unauthenticated`**（不公开），Scheduler 用 `--oidc-service-account-email` + `--oidc-token-audience` 认证；不能图省事开匿名。

## 成本护栏

- 只建上表免费资源；**不碰 VM / 静态 IP / GCS 东京桶**（收费点）。
- Budget Alert $1（0.5% 阈值）——任何意外扣费邮件告警。
- 月度用量：~1000 次调用/月，占 Functions 免费额度（200万）零头。

## 回滚

```bash
gcloud functions delete monitor --region=asia-northeast1
gcloud scheduler jobs delete monitor-dense monitor-hourly --location=asia-northeast1
# 或恢复 GitHub Actions(旧路径仍在): gh workflow enable monitor.yml
```
注意：GitHub 快照 `snapshot/rooms.json` 是停用前遗留，恢复后首次运行会重新建基线。

## 验证调度

`scheduler jobs describe monitor-dense --location=asia-northeast1` 看下一次运行时间；函数日志用 `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="monitor"'`。
