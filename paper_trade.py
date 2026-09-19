"""
PAPER TRADE — симуляция реального исполнения.

Читает funding из data/raw/, пишет сигналы в paper_trades.jsonl.
НЕ отправляет ордера. Реальное API — следующий шаг.
"""
import json, warnings
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data")
LOG = Path("paper_trades.jsonl")
STATE = Path("paper_state.json")
W = 100

def panel(t): print("="*W); print("  "+t); print("="*W)

def load_funding(sym, ex="binance"):
    p = DATA/f"raw/{ex}/{sym}_funding.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["funding_timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    return df.set_index("ts")["funding_rate"].resample("1D").sum()

def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"capital": 10000.0, "positions": {}, "pnl": 0.0, "started": None}

def save_state(s):
    STATE.write_text(json.dumps(s, indent=2, default=str))

def log_trade(trade):
    with LOG.open("a") as f:
        f.write(json.dumps(trade, default=str) + "\n")

def signal_today(sym, funding, min_rate=0.0001):
    """Long spot + short perp если funding > min_rate."""
    if funding is None or len(funding) < 2: return False
    today = funding.iloc[-1]
    return today > min_rate

def main():
    panel("PAPER TRADE — DAILY SIGNAL GENERATION")

    state = load_state()
    if state["started"] is None:
        state["started"] = datetime.now(timezone.utc).isoformat()

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"]
    print(f"  Tracked symbols: {len(symbols)}")
    print(f"  Capital: ${state['capital']:.2f}")
    print(f"  Started: {state['started']}")

    panel("SIGNALS TODAY")
    print(f"  {'symbol':<10}{'funding':>12}{'annualized':>12}{'signal':>10}{'action':>15}")
    print("  " + "-"*(W-2))

    opened = []
    for s in symbols:
        f = load_funding(s)
        if f is None: continue
        rate = float(f.iloc[-1])
        ann = rate * 3 * 365 * 100
        sig = signal_today(s, f)
        in_pos = s in state["positions"]

        action = ""
        if sig and not in_pos:
            action = "OPEN carry"
            state["positions"][s] = {
                "entry_ts": datetime.now(timezone.utc).isoformat(),
                "entry_rate": rate,
                "size_usd": state["capital"] * 0.1,
            }
            log_trade({"action": "open", "symbol": s, "rate": rate, "ts": datetime.now(timezone.utc).isoformat()})
            opened.append(s)
        elif not sig and in_pos:
            action = "CLOSE carry"
            pos = state["positions"].pop(s)
            days = (datetime.now(timezone.utc) - datetime.fromisoformat(pos["entry_ts"])).days
            pnl = pos["size_usd"] * pos["entry_rate"] * 3 * max(days, 1) / 1.5
            state["pnl"] += pnl
            log_trade({"action": "close", "symbol": s, "rate": rate, "pnl": pnl,
                       "ts": datetime.now(timezone.utc).isoformat()})
        elif in_pos:
            action = "HOLD"

        mark = "OK" if sig else "NO"
        print(f"  {s:<10}{rate:>12.6f}{ann:>11.2f}%{mark:>10}{action:>15}")

    state["capital"] = 10000.0 + state["pnl"]
    save_state(state)

    panel("STATE")
    print(f"  Open positions: {len(state['positions'])}")
    print(f"  Realized PnL:   ${state['pnl']:+.2f}")
    print(f"  Capital:        ${state['capital']:.2f}")
    print(f"  Trades logged:  {sum(1 for _ in LOG.open()) if LOG.exists() else 0}")

    panel("NEXT STEPS")
    print("  1. Cron запускать daily: python3 paper_trade.py")
    print("  2. Логи в paper_trades.jsonl — анализ через месяц")
    print("  3. Через 30 дней: replace signal with real API call")
    print("="*W)

if __name__ == "__main__":
    main()
