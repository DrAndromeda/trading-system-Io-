#!/usr/bin/env python3
"""
Сбор on-chain метрик и CVD.
- Blockchain.info (бесплатно, без ключа) — hashrate, active addresses, tx count
- Binance WebSocket aggTrade — CVD в реальном времени
- Binance REST — Open Interest History
"""
import requests, json, time, threading
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

DATA = Path("data/onchain"); DATA.mkdir(parents=True, exist_ok=True)

# === BLOCKCHAIN.INFO (free, no key) ===
def fetch_blockchain_metrics(days=365):
    """Базовые on-chain метрики BTC. Бесплатно, без ключа."""
    metrics = {}
    charts = {
        "hash-rate": "hashrate",
        "n-unique-addresses": "active_addresses",
        "n-transactions": "tx_count",
        "estimated-transaction-volume-usd": "tx_volume_usd",
        "market-price": "price",
        "miners-revenue": "miners_revenue",
    }
    for chart, name in charts.items():
        url = f"https://api.blockchain.info/charts/{chart}"
        try:
            r = requests.get(url, params={"timespan": f"{days}days", "format": "json"},
                             timeout=30)
            r.raise_for_status()
            d = r.json()
            metrics[name] = [
                {"ts": int(x["x"]) * 1000, "value": float(x["y"])} for x in d["values"]
            ]
            print(f"  [blockchain.info] {name}: {len(metrics[name])} точек")
        except Exception as e:
            print(f"  [blockchain.info] {name}: ошибка {e}")
        time.sleep(0.3)
    return metrics

# === BINANCE OPEN INTEREST (free, no key) ===
def fetch_open_interest(symbol="BTCUSDT", period="1d", limit=500):
    """История открытого интереса фьючерсов."""
    r = requests.get("https://fapi.binance.com/futures/data/openInterestHist",
                     params={"symbol": symbol, "period": period, "limit": limit},
                     timeout=30)
    r.raise_for_status()
    return [
        {"ts": int(x["timestamp"]), "oi": float(x["sumOpenInterest"]),
         "oi_value": float(x["sumOpenInterestValue"])}
        for x in r.json()
    ]

# === BINANCE LONG/SHORT RATIO (free, no key) ===
def fetch_long_short_ratio(symbol="BTCUSDT", period="1d", limit=500):
    """Соотношение лонгов и шортов."""
    r = requests.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                     params={"symbol": symbol, "period": period, "limit": limit},
                     timeout=30)
    r.raise_for_status()
    return [
        {"ts": int(x["timestamp"]), "ratio": float(x["longShortRatio"]),
         "long_pct": float(x["longAccount"]) * 100,
         "short_pct": float(x["shortAccount"]) * 100}
        for x in r.json()
    ]

# === CVD из aggTrade (последние N часов) ===
def fetch_cvd_binance(symbol="BTCUSDT", hours=24):
    """CVD через REST aggTrade (последние N часов)."""
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - hours * 3600 * 1000
    all_trades = []
    cur = start_ms
    while cur < end_ms:
        try:
            r = requests.get("https://fapi.binance.com/fapi/v1/aggTrades",
                             params={"symbol": symbol, "startTime": cur,
                                     "endTime": end_ms, "limit": 1000}, timeout=30)
            r.raise_for_status()
            k = r.json()
            if not k: break
            all_trades.extend(k)
            cur = k[-1]["T"] + 1
            if len(k) < 1000: break
            time.sleep(0.1)
        except Exception as e:
            print(f"  [cvd] ошибка: {e}")
            break

    if not all_trades:
        return None

    # CVD = накопленная (buy_vol - sell_vol)
    # isBuyerMaker=true → SELL (агрессивный продавец), false → BUY
    buy_vol = sell_vol = 0.0
    cvd_series = []
    for t in all_trades:
        qty = float(t["q"])
        if t["m"]:  # isBuyerMaker → market sell
            sell_vol += qty
        else:
            buy_vol += qty
        cvd_series.append({"ts": int(t["T"]), "cvd": buy_vol - sell_vol,
                           "price": float(t["p"])})

    return {
        "cvd_series": cvd_series[-5000:],
        "total_buy_vol": buy_vol,
        "total_sell_vol": sell_vol,
        "final_cvd": buy_vol - sell_vol,
        "n_trades": len(all_trades),
    }

# === MAIN ===
if __name__ == "__main__":
    print("=" * 80)
    print("  ON-CHAIN + ORDERFLOW DATA FETCHER")
    print("=" * 80)

    # 1. On-chain метрики BTC
    print("\n[1] Blockchain.info — метрики BTC (365 дней)")
    btc_metrics = fetch_blockchain_metrics(365)
    (DATA / "blockchain_info.json").write_text(
        json.dumps(btc_metrics, indent=2), encoding="utf-8")
    print(f"  → сохранено в {DATA}/blockchain_info.json")

    # 2. Open Interest
    print("\n[2] Binance Open Interest (BTC)")
    for sym in ["BTCUSDT", "ETHUSDT"]:
        try:
            oi = fetch_open_interest(sym, "1d", 500)
            (DATA / f"oi_{sym}.json").write_text(
                json.dumps(oi, indent=2), encoding="utf-8")
            print(f"  {sym}: {len(oi)} точек → {DATA}/oi_{sym}.json")
        except Exception as e:
            print(f"  {sym}: ошибка {e}")

    # 3. Long/Short ratio
    print("\n[3] Binance Long/Short Ratio (BTC)")
    for sym in ["BTCUSDT", "ETHUSDT"]:
        try:
            ls = fetch_long_short_ratio(sym, "1d", 500)
            (DATA / f"ls_ratio_{sym}.json").write_text(
                json.dumps(ls, indent=2), encoding="utf-8")
            print(f"  {sym}: {len(ls)} точек → {DATA}/ls_ratio_{sym}.json")
        except Exception as e:
            print(f"  {sym}: ошибка {e}")

    # 4. CVD за 24 часа
    print("\n[4] CVD из aggTrades (BTC, 24 часа)")
    cvd = fetch_cvd_binance("BTCUSDT", 24)
    if cvd:
        print(f"  Сделок: {cvd['n_trades']}")
        print(f"  Buy volume:  {cvd['total_buy_vol']:.2f}")
        print(f"  Sell volume: {cvd['total_sell_vol']:.2f}")
        print(f"  Final CVD:   {cvd['final_cvd']:+.2f}")
        (DATA / "cvd_btc.json").write_text(
            json.dumps(cvd, indent=2), encoding="utf-8")
        print(f"  → сохранено в {DATA}/cvd_btc.json")

    print("\n" + "=" * 80)
    print("  ГОТОВО")
    print("=" * 80)
