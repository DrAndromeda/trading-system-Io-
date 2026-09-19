#!/bin/bash
if pgrep -f live_collector.py > /dev/null; then
    echo "RUNNING (PID $(pgrep -f live_collector.py))"
    echo "Uptime: $(ps -o etime= -p $(pgrep -f live_collector.py) 2>/dev/null | tr -d ' ')"
    if [ -f /Users/andromeda/crypto-carry/logs/live.jsonl ]; then
        echo "Signals: $(wc -l < /Users/andromeda/crypto-carry/logs/live.jsonl | tr -d ' ')"
    else
        echo "Signals: 0 (no signals yet)"
    fi
    echo "---"
    tail -8 /Users/andromeda/crypto-carry/logs/live.log
else
    echo "NOT RUNNING"
fi
