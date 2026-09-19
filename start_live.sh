#!/bin/bash
cd /Users/andromeda/crypto-carry
if pgrep -f live_collector.py > /dev/null; then
    pkill -f live_collector.py
    sleep 1
fi
# Force remove bytecode cache
rm -rf __pycache__
# Run with explicit venv python
nohup /Users/andromeda/crypto-carry/.venv/bin/python3 live_collector.py >> logs/live.log 2>&1 &
echo "Started PID: $!"
