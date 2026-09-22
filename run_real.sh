#!/bin/bash
cd /Users/andromeda/crypto-carry
source .venv/bin/activate
pkill -f place_order_real.py 2>/dev/null
sleep 1
nohup python3 place_order_real.py >> logs/real.log 2>&1 &
echo "Started PID: $!"
