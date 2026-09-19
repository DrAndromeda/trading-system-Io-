#!/usr/bin/env python3
"""Сбор funding rates с Binance, Bybit, OKX. Сохраняет в parquet с полной мета-инфой."""
import requests, time, json
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data/raw")
DATA.mkdir(parents=True, exist_ok=True)

# === BINANCE ===
def binance_funding_info():
    """Интервалы funding для всех контрактов."""
    r = requests.get("https://fapi.binance.com/fapi/v1/fundingInfo", timeout=30)
    r.raise_for_status()
    out = {}
    for x in r.json():
        out[x["symbol"]] = {
            "interval_hours": int(x.get("fundingIntervalHours", 8)),
            "cap": x.get("adjustedFundingRateCap"),
            "floor": x.get("adjustedFundingRateFloor"),
        }
    return out

def binance_funding_history(symbol, start_ms, end_ms):
    """История funding. По 1000 записей за раз."""
    all_data = []
    cur = start_ms
    while cur < end_ms:
        r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                         params={"symbol": symbol, "startTime": cur, "endTime": end_ms, "limit": 1000},
                         timeout=30)
        if r.status_code != 200:
            break
        k = r.json()
        if not k:
            break
        all_data.extend(k)
        cur = k[-1]["fundingTime"] + 1
        if len(k) < 1000:
            break
        time.sleep(0.1)
    return all_data

def fetch_binance(symbols, years=5):
    info = binance_funding_info()
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - years * 365 * 24 * 3600 * 1000
    out_dir = DATA / "binance"
    out_dir.mkdir(parents=True, exist_ok=True)

    for sym in symbols:
        print(f"[binance] {sym}...")
        data = binance_funding_history(sym, start_ms, end_ms)
        if not data:
            print(f"  нет данных")
            continue
        df = pd.DataFrame(data)
        df["exchange"] = "binance"
        df["symbol"] = sym
        df["funding_rate"] = df["fundingRate"].astype(float)
        df["funding_timestamp"] = df["fundingTime"].astype("int64")
        df["interval_hours"] = info.get(sym, {}).get("interval_hours", 8)
        df["mark_price"] = df.get("markPrice", pd.Series([None]*len(df)))
        df = df[["exchange", "symbol", "funding_rate", "funding_timestamp", "interval_hours", "mark_price"]]
        df.to_parquet(out_dir / f"{sym}_funding.parquet", index=False)
        print(f"  сохранено {len(df)} записей")

# === BYBIT ===
def fetch_bybit(symbols, years=5):
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - years * 365 * 24 * 3600 * 1000
    out_dir = DATA / "bybit"
    out_dir.mkdir(parents=True, exist_ok=True)

    for sym in symbols:
        print(f"[bybit] {sym}...")
        rows = []
        cur_end = end_ms
        while cur_end > start_ms:
            r = requests.get("https://api.bybit.com/v5/market/funding/history",
                             params={"category": "linear", "symbol": sym,
                                     "startTime": start_ms, "endTime": cur_end, "limit": 200},
                             timeout=30)
            if r.status_code != 200:
                break
            k = r.json().get("result", {}).get("list", [])
            if not k:
                break
            rows.extend(k)
            cur_end = int(k[-1]["fundingRateTimestamp"]) - 1
            time.sleep(0.1)
        if not rows:
            print("  нет данных"); continue
        df = pd.DataFrame(rows)
        df["exchange"] = "bybit"
        df["symbol"] = sym
        df["funding_rate"] = df["fundingRate"].astype(float)
        df["funding_timestamp"] = df["fundingRateTimestamp"].astype("int64")
        df["interval_hours"] = 8
        df["mark_price"] = None
        df = df[["exchange", "symbol", "funding_rate", "funding_timestamp", "interval_hours", "mark_price"]]
        df.to_parquet(out_dir / f"{sym}_funding.parquet", index=False)
        print(f"  сохранено {len(df)} записей")

# === OKX ===
def fetch_okx(symbols, years=5):
    """OKX: /api/v5/public/funding-rate-history. Параметр after = timestamp в ms."""
    out_dir = DATA / "okx"
    out_dir.mkdir(parents=True, exist_ok=True)

    for sym in symbols:
        print(f"[okx] {sym}...")
        rows = []
        # OKX даёт по 100 записей, идём в прошлое через after
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - years * 365 * 24 * 3600 * 1000
        cur = end_ms
        for _ in range(300):  # макс 300 запросов * 100 = 30000 записей (хватит на 5 лет)
            try:
                r = requests.get("https://www.okx.com/api/v5/public/funding-rate-history",
                                 params={"instId": sym, "after": str(cur), "limit": 100},
                                 timeout=30)
                if r.status_code != 200:
                    print(f"  HTTP {r.status_code}, стоп")
                    break
                k = r.json().get("data", [])
                if not k:
                    break
                rows.extend(k)
                cur = int(k[-1]["fundingTime"]) - 1
                if cur < start_ms:
                    break
                time.sleep(0.15)
            except Exception as e:
                print(f"  ошибка: {e}")
                break
        if not rows:
            print("  нет данных"); continue
        df = pd.DataFrame(rows)
        df["exchange"] = "okx"
        df["symbol"] = sym
        df["funding_rate"] = df["fundingRate"].astype(float)
        df["funding_timestamp"] = df["fundingTime"].astype("int64")
        df["interval_hours"] = 8
        df["mark_price"] = None
        df = df[["exchange", "symbol", "funding_rate", "funding_timestamp", "interval_hours", "mark_price"]]
        df.to_parquet(out_dir / f"{sym}_funding.parquet", index=False)
        print(f"  сохранено {len(df)} записей")

if __name__ == "__main__":
    # Топ-20 перпов Binance USDT-M
    BINANCE_SYMS = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT",
        "DOTUSDT", "LTCUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT",
        "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "TIAUSDT",
    ]
    BYBIT_SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                  "AVAXUSDT", "LINKUSDT", "APTUSDT", "ARBUSDT", "SUIUSDT"]
    OKX_SYMS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
                "XRP-USDT-SWAP", "DOGE-USDT-SWAP"]

    print("=" * 60)
    print("  СБОР FUNDING RATES — 5 ЛЕТ")
    print("=" * 60)
    fetch_binance(BINANCE_SYMS, 5)
    fetch_bybit(BYBIT_SYMS, 5)
    fetch_okx(OKX_SYMS, 5)
    print("\nГотово. Данные в data/raw/{exchange}/")
