#!/bin/bash
cd /Users/andromeda/crypto-carry
source /Users/andromeda/crypto-carry/.venv/bin/activate
export TELEGRAM_TOKEN=""
export TELEGRAM_CHAT_ID=""
if pgrep -f live_collector.py > /dev/null; then
    echo "Already running (PID $(pgrep -f live_collector.py))"
    exit 0
fi
nohup python3 live_collector.py >> logs/live.log 2>&1 &
echo "Started PID: $!"
