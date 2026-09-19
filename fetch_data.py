import time, requests, pandas as pd
from pathlib import Path

OUT = Path("data"); OUT.mkdir(exist_ok=True)
SYMS = ["BTCUSDT", "ETHUSDT"]
START = int(pd.Timestamp("2020-01-01", tz="UTC").timestamp() * 1000)

def get(url, params):
    r = None
    for i in range(5):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 * (i + 1))
    r.raise_for_status()

def funding(sym):
    rows, t = [], START
    while True:
        d = get("https://fapi.binance.com/fapi/v1/fundingRate",
                {"symbol": sym, "startTime": t, "limit": 1000})
        if not d:
            break
        rows += d
        t = d[-1]["fundingTime"] + 1
        if len(d) < 1000:
            break
        time.sleep(0.2)
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["rate"] = df["fundingRate"].astype(float)
    return df[["time", "rate"]]

def klines(url, sym, limit):
    rows, t = [], START
    while True:
        d = get(url, {"symbol": sym, "interval": "1d", "startTime": t, "limit": limit})
        if not d:
            break
        rows += d
        t = d[-1][0] + 1
        if len(d) < limit:
            break
        time.sleep(0.2)
    df = pd.DataFrame(rows).iloc[:, :5]
    df.columns = ["t", "o", "h", "l", "c"]
    df["time"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df["close"] = df["c"].astype(float)
    return df[["time", "close"]]

for s in SYMS:
    funding(s).to_csv(OUT / f"{s}_funding.csv", index=False)
    klines("https://api.binance.com/api/v3/klines", s, 1000).to_csv(OUT / f"{s}_spot.csv", index=False)
    klines("https://fapi.binance.com/fapi/v1/klines", s, 1500).to_csv(OUT / f"{s}_perp.csv", index=False)
    print("done", s)
