#!/usr/bin/env python3
"""Сбор и анализ базиса квартальных фьючерсов BTC, ETH, XRP.
API: /fapi/v1/continuousKlines (CURRENT_QUARTER, NEXT_QUARTER)
Spot: /api/v3/klines
"""
import requests, time
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data/quarterly")
DATA.mkdir(parents=True, exist_ok=True)

# Активы для теста (PAXG исключён — нет квартальных фьючерсов)
ASSETS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
}

def fetch_spot_daily(symbol, start_ms, end_ms):
    """Спотовая цена (дневные свечи)."""
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={"symbol": symbol, "interval": "1d",
                             "startTime": start_ms, "endTime": end_ms, "limit": 1500},
                     timeout=30)
    r.raise_for_status()
    k = r.json()
    return pd.DataFrame({
        "ts": [int(x[0]) for x in k],
        "spot_close": [float(x[4]) for x in k],
    })

def fetch_quarterly_daily(pair, contract_type, start_ms, end_ms):
    """Квартальный фьючерс (дневные свечи)."""
    r = requests.get("https://fapi.binance.com/fapi/v1/continuousKlines",
                     params={"pair": pair, "contractType": contract_type,
                             "interval": "1d", "startTime": start_ms,
                             "endTime": end_ms, "limit": 1500},
                     timeout=30)
    r.raise_for_status()
    k = r.json()
    if not k:
        return None
    return pd.DataFrame({
        "ts": [int(x[0]) for x in k],
        "fut_close": [float(x[4]) for x in k],
    })

def build_basis_table(symbol, asset_name, years=3):
    """Собирает базис по кварталам."""
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - years * 365 * 24 * 3600 * 1000

    print(f"\n[{asset_name}] Загрузка спота...")
    spot = fetch_spot_daily(symbol, start_ms, end_ms)
    print(f"  Спот: {len(spot)} свечей")

    all_rows = []
    for ct in ["CURRENT_QUARTER", "NEXT_QUARTER"]:
        print(f"[{asset_name}] Загрузка {ct}...")
        try:
            q = fetch_quarterly_daily(symbol, ct, start_ms, end_ms)
            if q is None or q.empty:
                print(f"  {ct}: нет данных")
                continue
            print(f"  {ct}: {len(q)} свечей")

            merged = pd.merge(spot, q, on="ts", how="inner")
            if merged.empty:
                print(f"  {ct}: после merge нет данных")
                continue

            merged["asset"] = asset_name
            merged["contract_type"] = ct
            merged["basis_pct"] = (merged["fut_close"] - merged["spot_close"]) / merged["spot_close"] * 100
            merged["annualized_pct"] = merged["basis_pct"] * (365 / 90)  # ~90 дней до экспирации
            all_rows.append(merged)
        except Exception as e:
            print(f"  {ct}: ошибка {e}")
        time.sleep(0.3)

    if not all_rows:
        return None
    return pd.concat(all_rows, ignore_index=True)

if __name__ == "__main__":
    print("=" * 80)
    print("  АНАЛИЗ БАЗИСА КВАРТАЛЬНЫХ ФЬЮЧЕРСОВ")
    print("=" * 80)

    all_data = []
    for name, symbol in ASSETS.items():
        df = build_basis_table(symbol, name, years=3)
        if df is not None:
            all_data.append(df)

    if not all_data:
        print("\n❌ Нет данных. Возможно, квартальные фьючерсы недоступны.")
        exit()

    df = pd.concat(all_data, ignore_index=True)
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["year"] = df["date"].dt.year
    df["quarter"] = df["date"].dt.to_period("Q")

    # Убираем выбросы (битые свечи)
    df = df[df["basis_pct"].abs() < 20]

    print(f"\n{'='*80}")
    print(f"  РЕЗУЛЬТАТЫ — {len(df)} наблюдений")
    print(f"{'='*80}")

    # По активам
    print("\n─── Средний базис по активам ───")
    print(f"{'Актив':<8}{'Наблюдений':>12}{'Средний базис':>16}{'Ann.%':>10}")
    for asset in df["asset"].unique():
        sub = df[df["asset"] == asset]
        avg_basis = sub["basis_pct"].mean()
        avg_ann = sub["annualized_pct"].mean()
        print(f"  {asset:<8}{len(sub):>12}{avg_basis:>15.2f}%{avg_ann:>9.2f}%")

    # По годам
    print("\n─── Средний базис по годам (годовых %) ───")
    print(f"{'Год':<6}{'Наблюдений':>12}{'Ann.%':>12}{'Медиана':>12}{'P10':>10}{'P90':>10}")
    for year in sorted(df["year"].unique()):
        sub = df[df["year"] == year]
        ann = sub["annualized_pct"]
        print(f"  {year:<6}{len(sub):>12}{ann.mean():>11.2f}%{ann.median():>11.2f}%"
              f"{ann.quantile(0.1):>9.2f}%{ann.quantile(0.9):>9.2f}%")

    # По кварталам
    print("\n─── Базис по кварталам (годовых %) ───")
    by_q = df.groupby("quarter")["annualized_pct"].mean()
    for q, v in by_q.items():
        bar = "█" * max(1, int(abs(v) / 3))
        sign = "+" if v > 0 else "-"
        print(f"  {q}  {v:>+8.2f}%  {sign}{bar}")

    # После издержек
    print("\n─── NET CARRY после издержек ───")
    print("  Издержки round-trip: 0.28% (spot 0.2% + perp 0.08%)")
    print("  Держим 90 дней → издержки в годовых: 0.28% × 4 = 1.12%")
    print()
    for year in sorted(df["year"].unique()):
        sub = df[df["year"] == year]
        gross = sub["annualized_pct"].mean()
        net = gross - 1.12
        mark = "✅" if net > 5 else "⚠️" if net > 0 else "❌"
        print(f"  {year}: gross {gross:>+7.2f}% → net {net:>+7.2f}%  {mark}")

    # Сохраняем
    out = DATA / "basis_all.parquet"
    df.to_parquet(out, index=False)
    print(f"\n💾 Сохранено: {out}")
