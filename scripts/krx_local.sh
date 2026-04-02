#!/bin/bash
# KRX 전종목 크롤러 — 로컬 WSL 원클릭 실행
#
# crontab 등록 (평일 15:55 KST):
#   crontab -e
#   55 15 * * 1-5 /home/arctu/stock-bot/scripts/krx_local.sh >> /tmp/krx_update.log 2>&1
#
# 수동 실행:
#   bash /home/arctu/stock-bot/scripts/krx_local.sh
#   bash /home/arctu/stock-bot/scripts/krx_local.sh --date 20260402

set -e
cd /home/arctu/stock-bot
git pull --quiet
pip install -r scripts/requirements_actions.txt --quiet
python3 scripts/krx_update.py "$@"
