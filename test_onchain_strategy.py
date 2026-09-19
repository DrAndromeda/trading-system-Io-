#!/usr/bin/env python3
"""Бэктест простых on-chain стратегий на исторических данных."""
import json
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path("data/onchain")

def load_blockchain():
    d = json.loads((DATA / "blockchain_info.json").read_text())
    df = pd.DataFrame(d["price"]).rename(columns={"value": "price", "ts": "ts"})
    for key in ["hashrate", "active_addresses", "tx_count"]:
        if key in d:
            tmp = pd.DataFrame(d[key]).rename(columns={"value": key})
            df = pd.merge(df, tmp, on="ts", how="outer")
    df = df.sort_values("ts").reset_index(drop=True)
    return df

def load_oi(symbol="BTCUSDT"):
    d = json.loads((DATA / f"oi_{symbol}.json").read_text())
    return pd.DataFrame(d).sort_values("ts").reset_index(drop=True)

def load_ls(symbol="BTCUSDT"):
    d = json.loads((DATA / f"ls_ratio_{symbol}.json").read_text())
    return pd.DataFrame(d).sort_values("ts").reset_index(drop=True)

def backtest(df, signal_fn, name, fee=0.0016):
    """signal_fn(df, i) -> 1/0/-1"""
    n = len(df)
    pos = np.zeros(n)
    for i in range(20, n):
        pos[i] = signal_fn(df, i)
    ret = df["price"].pct_change().fillna(0).values
    turn = np.abs(np.diff(np.concatenate([[0], pos[:-1]])))
    eq = pos[:-1] * ret[1:] - turn[1:] * fee
    total = (np.prod(1 + eq) - 1) * 100
    dd = ((np.cumprod(1+eq) / np.maximum.accumulate(np.cumprod(1+eq))) - 1).min() * 100
    wins = (eq > 0).sum(); losses = (eq <= 0).sum()
    wr = wins / max(wins+losses, 1) * 100
    return {"name": name, "total_pct": total, "max_dd": dd, "winrate": wr,
            "n_bars": len(eq)}

print("=" * 80)
print("  ON-CHAIN STRATEGY BACKTEST")
print("=" * 80)

df = load_blockchain()
print(f"Данных: {len(df)} дней с {df['ts'].iloc[0]} по {df['ts'].iloc[-1]}")

# Стратегия 1: hashrate momentum
def sig_hashrate(df, i):
    h = df["hashrate"].iloc[:i+1]
    if len(h) < 30: return 0
    ma30 = h.tail(30).mean()
    return 1 if h.iloc[-1] > ma30 else 0

# Стратегия 2: active addresses momentum
def sig_active(df, i):
    a = df["active_addresses"].iloc[:i+1]
    if len(a) < 30: return 0
    ma30 = a.tail(30).mean()
    return 1 if a.iloc[-1] > ma30 else 0

# Стратегия 3: price + hashrate confirm
def sig_combo(df, i):
    if i < 30: return 0
    p = df["price"].iloc[:i+1]
    h = df["hashrate"].iloc[:i+1]
    p_ma = p.tail(30).mean()
    h_ma = h.tail(30).mean()
    if p.iloc[-1] > p_ma and h.iloc[-1] > h_ma:
        return 1
    return 0

strategies = [
    ("hashrate momentum", sig_hashrate),
    ("active addresses", sig_active),
    ("price+hashrate combo", sig_combo),
]

results = []
for name, fn in strategies:
    r = backtest(df, fn, name)
    results.append(r)
    print(f"  {r['name']:<25} total={r['total_pct']:>+8.1f}%  "
          f"DD={r['max_dd']:>+6.1f}%  WR={r['winrate']:>5.1f}%")

# Buy & hold
p = df["price"].values
bh = (p[-1] / p[0] - 1) * 100
print(f"  {'Buy & hold BTC':<25} total={bh:>+8.1f}%")

print()
print("Порог: total > buy & hold = стратегия работает")
