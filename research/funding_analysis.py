#!/usr/bin/env python3
"""Анализ funding: распределение по годам, активам, режимам. Без оптимизации."""
import pandas as pd
import numpy as np
from pathlib import Path

DATA = Path("data/raw")

def load_all(exchange="binance"):
    dir_ = DATA / exchange
    if not dir_.exists(): return None
    frames = []
    for f in dir_.glob("*_funding.parquet"):
        df = pd.read_parquet(f)
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None

def year_from_ts(ts_ms):
    return pd.to_datetime(ts_ms, unit="ms", utc=True).dt.year

def analyze(df, exchange="binance"):
    if df is None or df.empty:
        print(f"{exchange}: нет данных"); return
    df = df.copy()
    df["year"] = year_from_ts(df["funding_timestamp"])
    df["daily_equiv"] = df["funding_rate"] * (24 / df["interval_hours"])
    df["annual_equiv"] = df["daily_equiv"] * 365

    print(f"\n{'='*85}")
    print(f"  {exchange.upper()} — {df['symbol'].nunique()} символов, {len(df)} записей")
    print(f"{'='*85}")

    # Распределение по годам
    print("\n─── Средний funding по годам (годовых %) ───")
    print(f"{'Год':<6}{'Записей':>10}{'Средний':>12}{'Медиана':>12}{'P10':>10}{'P90':>10}")
    for year in sorted(df["year"].unique()):
        sub = df[df["year"] == year]
        ann = sub["annual_equiv"] * 100
        print(f"{year:<6}{len(sub):>10}{ann.mean():>11.2f}%{ann.median():>11.2f}%"
              f"{ann.quantile(0.1):>9.2f}%{ann.quantile(0.9):>9.2f}%")

    # Топ-5 и worst-5 активов по среднему
    print("\n─── Топ-5 активов по среднему funding (годовых %) ───")
    by_sym = df.groupby("symbol")["annual_equiv"].mean() * 100
    by_sym = by_sym.sort_values(ascending=False)
    for sym in by_sym.head(5).index:
        print(f"  {sym:<14}{by_sym[sym]:>8.2f}%")
    print("\n─── Худшие 5 (могут быть убыточными) ───")
    for sym in by_sym.tail(5).index:
        print(f"  {sym:<14}{by_sym[sym]:>8.2f}%")

    # Процент времени положительный
    pos_pct = (df["funding_rate"] > 0).mean() * 100
    print(f"\n─── Общее ───")
    print(f"  Время с положительным funding: {pos_pct:.1f}%")
    print(f"  Средний интервал: {df['interval_hours'].mode()[0]} часов")

    # Режимы (год-квартал)
    print("\n─── По кварталам (средний ann. %) ───")
    df["quarter"] = pd.to_datetime(df["funding_timestamp"], unit="ms", utc=True).dt.to_period("Q")
    by_q = df.groupby("quarter")["annual_equiv"].mean() * 100
    for q, v in by_q.items():
        bar = "█" * max(1, int(abs(v) / 2))
        sign = "+" if v > 0 else "-"
        print(f"  {q}  {v:>+7.2f}%  {sign}{bar}")

if __name__ == "__main__":
    for ex in ["binance", "bybit", "okx"]:
        df = load_all(ex)
        analyze(df, ex)
