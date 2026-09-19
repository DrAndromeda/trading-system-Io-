import json, warnings
import numpy as np, pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")
DATA = Path("data")
W = 100

def panel(t): print("="*W); print("  "+t); print("="*W)

def load_funding(ex, sym):
    p = DATA/f"raw/{ex}/{sym}_funding.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["funding_timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    return df.set_index("ts")["funding_rate"].resample("1D").sum()

def load_all(ex):
    d = DATA/f"raw/{ex}"
    return {f.stem.replace("_funding",""): load_funding(ex, f.stem.replace("_funding",""))
            for f in d.glob("*_funding.parquet")
            if len(pd.read_parquet(f)) > 100}

def load_basis():
    p = DATA/"quarterly/basis_all.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    return df

def metrics(ret):
    ret = pd.Series(ret).dropna()
    if len(ret) == 0: return dict(total=0, cagr=0, sharpe=0, dd=0, n=0)
    cum = (1+ret).cumprod()
    years = len(ret)/365
    cagr = (cum.iloc[-1]**(1/years)-1)*100 if years>0 and cum.iloc[-1]>0 else -100
    dd = ((cum/cum.cummax())-1).min()*100
    sh = ret.mean()/ret.std()*np.sqrt(365) if ret.std()>0 else 0
    return dict(total=(cum.iloc[-1]-1)*100, cagr=cagr, sharpe=sh, dd=dd, n=len(ret))

# ---------- REALISTIC CARRY ----------
def carry_realistic(funding, cap=1.5, slip_per_leg=0.0005, basis_vol=0.0003):
    """
    Carry с реалистичными издержками:
    - funding (3 выплаты/день)
    - slippage на rebalance (ежедневный 0.05% за ногу)
    - basis noise (spot vs perp расходятся)
    """
    rng = np.random.default_rng(42)
    daily = funding.where(funding > 0, 0.0)
    funding_pnl = daily * 3 / cap
    n = len(funding_pnl)
    # basis noise — гауссов шум, при закрытии реализуется
    basis_noise = pd.Series(rng.normal(0, basis_vol, n), index=funding_pnl.index)
    basis_pnl = -basis_noise.diff().fillna(0) * 0.5
    # slippage: если позиция меняется (funding переходит через 0), платим 0.05% × 2
    pos_change = (daily > 0).astype(int).diff().abs().fillna(0)
    slip = pos_change * slip_per_leg * 2
    return funding_pnl + basis_pnl - slip

def portfolio_realistic(funding_dict, cap=1.5):
    idx = None
    for s in funding_dict.values():
        idx = s.index if idx is None else idx.union(s.index)
    rets = []
    for f in funding_dict.values():
        r = carry_realistic(f.reindex(idx).fillna(0), cap=cap)
        rets.append(r)
    return pd.concat(rets, axis=1).mean(axis=1)

# ---------- CROSS-EXCHANGE ----------
def cross_ex(fb, fy, min_spread=0.0002, cost=0.0004):
    common = sorted(set(fb.keys()) & set(fy.keys()))
    out = {}
    for s in common:
        if fb[s] is None or fy[s] is None: continue
        idx = fb[s].index.union(fy[s].index)
        b = fb[s].reindex(idx).fillna(0); y = fy[s].reindex(idx).fillna(0)
        spread = (b - y).abs()
        active = (spread > min_spread).astype(float)
        pnl = active * spread * 3 / 1.5 - active.diff().abs().fillna(0) * cost
        out[s] = pnl
    return out

# ---------- MAIN ----------
def main():
    panel("CARRY v6 FINAL — REALISTIC COSTS + MULTI-ASSET + CROSS-EX")

    binance = load_all("binance")
    bybit = load_all("bybit")
    print(f"  binance: {len(binance)}  bybit: {len(bybit)}")

    # PANEL 1: realistic individual
    panel("PANEL 1: REALISTIC INDIVIDUAL (with basis noise + slippage)")
    print(f"  {'symbol':<10}{'days':>6}{'CAGR%':>9}{'Sharpe':>8}{'DD%':>8}")
    print("  " + "-"*(W-2))
    for s, f in sorted(binance.items()):
        if f is None: continue
        r = carry_realistic(f)
        m = metrics(r)
        print(f"  {s:<10}{m['n']:>6}{m['cagr']:>+8.2f}%{m['sharpe']:>+8.2f}{m['dd']:>+7.2f}%")

    # PANEL 2: realistic portfolio
    panel("PANEL 2: REALISTIC PORTFOLIO")
    r_port = portfolio_realistic(binance)
    m = metrics(r_port)
    print(f"  Equal weight realistic: total {m['total']:+.1f}%  CAGR {m['cagr']:+.2f}%  "
          f"Sharpe {m['sharpe']:+.2f}  DD {m['dd']:+.2f}%  n={m['n']}")

    # PANEL 3: per-year
    panel("PANEL 3: PER-YEAR (realistic)")
    df = r_port.to_frame("ret"); df["year"] = df.index.year
    print(f"  {'year':>6}{'ret%':>10}{'Sharpe':>8}{'DD%':>8}")
    print("  " + "-"*(W-2))
    for y, g in df.groupby("year"):
        m = metrics(g["ret"])
        print(f"  {y:>6}{m['total']:>+9.2f}%{m['sharpe']:>+8.2f}{m['dd']:>+7.2f}%")

    # PANEL 4: cross-exchange realistic
    panel("PANEL 4: CROSS-EXCHANGE (realistic)")
    if len(bybit) > 0:
        spreads = cross_ex(binance, bybit)
        idx = None
        for s in spreads.values(): idx = s.index if idx is None else idx.union(s.index)
        port = pd.concat([s.reindex(idx).fillna(0) for s in spreads.values()], axis=1).mean(axis=1)
        m = metrics(port)
        print(f"  Portfolio: CAGR {m['cagr']:+.2f}%  Sharpe {m['sharpe']:+.2f}  DD {m['dd']:+.2f}%")

    # PANEL 5: combined portfolio (carry + cross-ex)
    panel("PANEL 5: COMBINED (carry + cross-ex, 70/30)")
    if len(bybit) > 0:
        idx_c = r_port.index.union(port.index)
        r_carry = r_port.reindex(idx_c).fillna(0)
        r_cross = port.reindex(idx_c).fillna(0)
        for w in [1.0, 0.8, 0.7, 0.5]:
            r = w * r_carry + (1-w) * r_cross
            m = metrics(r)
            print(f"  w_carry={w:.1f}  CAGR {m['cagr']:+6.2f}%  Sharpe {m['sharpe']:+6.2f}  DD {m['dd']:+6.2f}%")

    # PANEL 6: verdict
    panel("PANEL 6: FINAL VERDICT")
    m_port = metrics(r_port)
    print(f"  Realistic Sharpe (with costs): {m_port['sharpe']:.2f}")
    print(f"  Realistic CAGR:                {m_port['cagr']:.2f}%")
    print(f"  Realistic DD:                  {m_port['dd']:.2f}%")
    print(f"\n  vs previous artifact (Sharpe 15) — now honest numbers")
    print(f"  Next: paper trade 30 days, then live on Bybit")
    print("="*W)

if __name__ == "__main__":
    main()
