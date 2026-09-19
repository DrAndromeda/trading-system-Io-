#!/usr/bin/env python3
"""Проверка on-chain стратегий на бычьем и медвежьем периодах."""
import json
import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path("data/onchain")

def load_blockchain():
    d = json.loads((DATA / "blockchain_info.json").read_text())
    df = pd.DataFrame(d["price"]).rename(columns={"value": "price", "ts": "ts"})
    for key in ["hashrate", "active_addresses"]:
        tmp = pd.DataFrame(d[key]).rename(columns={"value": key})
        df = pd.merge(df, tmp, on="ts", how="outer")
    df = df.sort_values("ts").reset_index(drop=True).ffill().dropna()
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def backtest(df, signal_fn, fee=0.0016):
    n = len(df)
    if n < 40: return None
    pos = np.zeros(n)
    for i in range(30, n):
        pos[i] = signal_fn(df, i)
    ret = df["price"].pct_change().fillna(0).values
    turn = np.abs(np.diff(pos, prepend=0.0))
    eq = pos[:-1] * ret[1:] - turn[1:] * fee
    if len(eq) == 0: return None
    cum = np.cumprod(1 + eq)
    total = (cum[-1] - 1) * 100
    dd = ((cum / np.maximum.accumulate(cum)) - 1).min() * 100
    exposure = (pos != 0).mean() * 100
    return {"total": total, "dd": dd, "exposure": exposure, "n": len(eq)}

def sig_price_hashrate(df, i):
    if i < 30: return 0
    p = df["price"].iloc[:i+1]; h = df["hashrate"].iloc[:i+1]
    return 1 if p.iloc[-1] > p.tail(30).mean() and h.iloc[-1] > h.tail(30).mean() else 0

df = load_blockchain()
print("=" * 80)
print("  ON-CHAIN на 2 подпериодах")
print("=" * 80)
print(f"Данных: {len(df)} дней ({df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()})")

# Разбиваем на 2 половины
mid = len(df) // 2
parts = [("Первая половина", df.iloc[:mid]),
         ("Вторая половина", df.iloc[mid:])]

for name, part in parts:
    part = part.reset_index(drop=True)
    if len(part) < 60: continue
    r = backtest(part, sig_price_hashrate)
    bh = (part["price"].iloc[-1] / part["price"].iloc[0] - 1) * 100
    if r:
        alpha = r["total"] - bh
        print(f"\n{name} ({part['date'].iloc[0].date()} → {part['date'].iloc[-1].date()})")
        print(f"  Стратегия: {r['total']:+.1f}%  DD {r['dd']:+.1f}%  экспозиция {r['exposure']:.0f}%")
        print(f"  Buy & hold: {bh:+.1f}%")
        print(f"  Альфа: {alpha:+.1f}%  {'✅' if alpha > 0 else '❌'}")
