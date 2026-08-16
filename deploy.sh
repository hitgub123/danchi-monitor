#!/bin/bash
# 从仓库根复制单源模块到临时构建目录, 再 gcloud functions deploy。
set -euo pipefail
PROJECT="${GCP_PROJECT:-danchi-monitor}"
REGION="asia-northeast1"
BUILD_DIR=".build/function"
rm -rf "$BUILD_DIR"; mkdir -p "$BUILD_DIR"
cp gcp_monitor.py actions_monitor.py ur_api.py models.py score.py notify.py costtime.py config.py schedule.py config.actions.yaml "$BUILD_DIR"/
# Cloud Functions gen2 Python 要求入口文件叫 main.py
mv "$BUILD_DIR/gcp_monitor.py" "$BUILD_DIR/main.py"
cp requirements.function.txt "$BUILD_DIR/requirements.txt"
gcloud config set project "$PROJECT"
gcloud functions deploy monitor \
  --gen2 --runtime python312 --trigger-http --no-allow-unauthenticated \
  --region="$REGION" --source="$BUILD_DIR" --entry-point=monitor \
  --timeout=540 --memory=512MB
