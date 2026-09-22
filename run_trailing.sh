#!/bin/bash
cd /Users/andromeda/crypto-carry
source .venv/bin/activate
pkill -f update_trailing_stops.py 2>/dev/null
sleep 1
nohup python3 update_trailing_stops.py >> logs/trailing.log 2>&1 &
echo "Started PID: $!"
