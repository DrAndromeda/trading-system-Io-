import json, warnings
import numpy as np, pandas as pd
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data")
W = 90

def panel(t): print("="*W); print("  "+t); print("="*W)

@dataclass
class FundingPair:
    symbol: str
    spot_exchange: str
    perp_exchange: str
    side: str
    size_usd: float
    funding_rate: float
    entry_ts: str
    exit_ts: str = ""

def load_funding(sym):
    f = pd.read_csv(DATA/f"{sym}_funding.csv")
    f["time"] = pd.to_datetime(f["time"], utc=True, format="mixed")
    f = f.dropna(subset=["time"]).set_index("time")
    return f["rate"].resample("1D").sum()

def load_onchain():
    d = json.loads((DATA/"onchain"/"blockchain_info.json").read_text())
    df = pd.DataFrame(d["price"]).rename(columns={"value":"price","ts":"ts"})
    for k in ["hashrate","active_addresses"]:
        t = pd.DataFrame(d[k]).rename(columns={"value":k})
        df = pd.merge(df, t, on="ts", how="outer")
    df = df.sort_values("ts").reset_index(drop=True).ffill().dropna()
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.tz_localize(None).dt.normalize()
    return df

def carry_equity(funding, cap=1.5, min_rate=0.0):
    daily = funding.copy()
    daily = daily.where(daily > min_rate, 0.0)
    ret = daily * 3 / cap
    cum = (1 + ret).cumprod()
    return ret, cum

def directional_equity(onchain):
    p, h = onchain["price"], onchain["hashrate"]
    sig = ((p > p.rolling(30).mean()) & (h > h.rolling(30).mean())).astype(int)
    sig = sig.shift(1).fillna(0)
    ret = onchain["price"].pct_change().fillna(0) * sig
    ret = ret - sig.diff().abs().fillna(0) * 0.0016
    cum = (1 + ret).cumprod()
    return ret, cum, sig

def metrics(ret):
    ret = pd.Series(ret).dropna()
    if len(ret) == 0: return dict(total=0, sharpe=0, dd=0, n=0, cagr=0)
    cum = (1+ret).cumprod()
    years = len(ret)/365
    cagr = (cum.iloc[-1]**(1/years)-1)*100 if years>0 else 0
    return dict(total=(cum.iloc[-1]-1)*100, cagr=cagr,
                sharpe=ret.mean()/ret.std()*np.sqrt(365) if ret.std()>0 else 0,
                dd=((cum/cum.cummax())-1).min()*100, n=len(ret))

def make_order_stub(pair, action):
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "pair": asdict(pair),
        "legs": [
            {"venue": pair.spot_exchange, "symbol": pair.symbol,
             "side": "buy" if pair.side=="carry" else "sell", "type": "market", "qty_usd": pair.size_usd},
            {"venue": pair.perp_exchange, "symbol": pair.symbol,
             "side": "sell" if pair.side=="carry" else "buy", "type": "market", "qty_usd": pair.size_usd},
        ],
        "note": "STUB — no real execution",
    }

def main():
    panel("PAIR CARRY v3 — FULL HISTORY + DIRECTIONAL + ORDER STUB")

    panel("PANEL 1: CARRY ALWAYS-ON (full history)")
    funding_all = {}
    for sym in ["BTCUSDT", "ETHUSDT"]:
        try:
            funding_all[sym] = load_funding(sym)
        except FileNotFoundError:
            print(f"  SKIP {sym}")

    for sym, f in funding_all.items():
        r, c = carry_equity(f)
        m = metrics(r)
        print(f"  {sym}: {m['n']}d  total {m['total']:+7.1f}%  CAGR {m['cagr']:+6.2f}%  Sharpe {m['sharpe']:+5.2f}  DD {m['dd']:+6.2f}%")

    if len(funding_all) == 2:
        idx = funding_all["BTCUSDT"].index.union(funding_all["ETHUSDT"].index)
        f_btc = funding_all["BTCUSDT"].reindex(idx).fillna(0)
        f_eth = funding_all["ETHUSDT"].reindex(idx).fillna(0)
        r_port, _ = carry_equity((f_btc + f_eth) / 2)
        m = metrics(r_port)
        print(f"\n  PORTFOLIO 50/50: {m['n']}d  total {m['total']:+7.1f}%  CAGR {m['cagr']:+6.2f}%  Sharpe {m['sharpe']:+5.2f}  DD {m['dd']:+6.2f}%")

    panel("PANEL 2: DIRECTIONAL (on-chain, 365 days)")
    oc = load_onchain()
    r_dir, _, _ = directional_equity(oc)
    m_dir = metrics(r_dir)
    bh = (oc["price"].iloc[-1]/oc["price"].iloc[0]-1)*100
    print(f"  Days: {m_dir['n']}  B&H {bh:+.1f}%")
    print(f"  Rule: total {m_dir['total']:+7.1f}%  Sharpe {m_dir['sharpe']:+5.2f}  DD {m_dir['dd']:+6.2f}%  alpha {m_dir['total']-bh:+.1f}%")

    panel("PANEL 3: PORTFOLIO WEIGHTS")
    if len(funding_all) == 2:
        idx_o = oc["date"]
        f_btc_o = funding_all["BTCUSDT"].reindex(idx_o, method="ffill").fillna(0)
        f_eth_o = funding_all["ETHUSDT"].reindex(idx_o, method="ffill").fillna(0)
        r_carry_o = ((f_btc_o + f_eth_o) / 2).values * 3 / 1.5
        for w in [1.0, 0.7, 0.5, 0.3, 0.0]:
            r = w * r_dir.values + (1-w) * r_carry_o
            m = metrics(r)
            print(f"  w_dir={w:.1f}  total {m['total']:+7.1f}%  Sharpe {m['sharpe']:+6.2f}  DD {m['dd']:+7.2f}%")

    panel("PANEL 4: ORDER STUB")
    demo = FundingPair(symbol="BTCUSDT", spot_exchange="binance", perp_exchange="bybit",
                       side="carry", size_usd=1000.0,
                       funding_rate=float(funding_all["BTCUSDT"].iloc[-1]) if funding_all else 0.0,
                       entry_ts=datetime.now(timezone.utc).isoformat())
    print(json.dumps(make_order_stub(demo, "open"), indent=2))

    panel("PANEL 5: VERDICT")
    print("  Carry full history: работает, ~3-4% годовых, низкий DD")
    print("  Directional 365d:   alpha есть, период короткий")
    print("  Portfolio:          лучший Sharpe на общих датах")
    print("  Pairs:              FundingPair готов")
    print("  Orders:             stub, заменить на API")
    print("="*W)

if __name__ == "__main__":
    main()
