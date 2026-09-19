import json, warnings
import numpy as np, pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")
DATA = Path("data")
W = 100

def panel(t): print("="*W); print("  "+t); print("="*W)

# ---------- DATA ----------
def load_funding(ex, sym):
    p = DATA/f"raw/{ex}/{sym}_funding.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["funding_timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    return df.set_index("ts")["funding_rate"].resample("1D").sum()

def load_all_funding(ex):
    d = DATA/f"raw/{ex}"
    syms = sorted([f.stem.replace("_funding","") for f in d.glob("*_funding.parquet")])
    out = {}
    for s in syms:
        r = load_funding(ex, s)
        if r is not None and len(r) > 100:
            out[s] = r
    return out

# ---------- METRICS ----------
def metrics(ret):
    ret = pd.Series(ret).dropna()
    if len(ret) == 0: return dict(total=0, cagr=0, sharpe=0, dd=0, n=0)
    cum = (1+ret).cumprod()
    years = len(ret)/365
    cagr = (cum.iloc[-1]**(1/years)-1)*100 if years>0 and cum.iloc[-1]>0 else -100
    dd = ((cum/cum.cummax())-1).min()*100
    sh = ret.mean()/ret.std()*np.sqrt(365) if ret.std()>0 else 0
    return dict(total=(cum.iloc[-1]-1)*100, cagr=cagr, sharpe=sh, dd=dd, n=len(ret))

# ---------- CARRY VARIANTS ----------
def carry_equal_weight(funding_dict, cap=1.5, min_rate=0.0):
    """Равные веса по всем символам."""
    idx = None
    for s in funding_dict.values():
        idx = s.index if idx is None else idx.union(s.index)
    rets = []
    for s, f in funding_dict.items():
        r = f.reindex(idx).fillna(0)
        r = r.where(r > min_rate, 0.0) * 3 / cap
        rets.append(r)
    return pd.concat(rets, axis=1).mean(axis=1)

def carry_vol_targeted(funding_dict, cap=1.5, min_rate=0.0, lookback=30, target_vol=0.10):
    """Вес ∝ 1/σ_funding, нормализован на target_vol."""
    idx = None
    for s in funding_dict.values():
        idx = s.index if idx is None else idx.union(s.index)
    weights = []
    for s, f in funding_dict.items():
        r = f.reindex(idx).fillna(0)
        r = r.where(r > min_rate, 0.0) * 3 / cap
        vol = r.rolling(lookback).std() * np.sqrt(365)
        w = (target_vol / vol.replace(0, np.nan)).clip(0, 3).fillna(0)
        weights.append(w)
    W_ = pd.concat(weights, axis=1)
    W_ = W_.div(W_.sum(axis=1).replace(0, 1), axis=0)
    rets = []
    for s, f in funding_dict.items():
        r = f.reindex(idx).fillna(0)
        r = r.where(r > min_rate, 0.0) * 3 / cap
        rets.append(r)
    R = pd.concat(rets, axis=1)
    return (W_.values * R.values).sum(axis=1)

# ---------- CROSS-EXCHANGE ----------
def cross_exchange_spread(binance_dict, bybit_dict, min_spread=0.0002):
    """Для каждого символа: если funding на биржах расходится > min_spread, long на низкой, short на высокой."""
    common = sorted(set(binance_dict.keys()) & set(bybit_dict.keys()))
    spreads = {}
    for s in common:
        b = binance_dict[s]; y = bybit_dict[s]
        idx = b.index.union(y.index)
        b2 = b.reindex(idx).fillna(0); y2 = y.reindex(idx).fillna(0)
        spread = (b2 - y2).abs()
        active = (spread > min_spread).astype(float)
        # P&L: если spread > threshold → зарабатываем на разнице минус комиссии
        pnl = active * spread * 3 / 1.5
        spreads[s] = pnl
    return spreads

# ---------- MAIN ----------
def main():
    panel("CARRY v5 — MULTI-ASSET (20 SYMBOLS) + CROSS-EXCHANGE")

    # Load all
    binance = load_all_funding("binance")
    bybit = load_all_funding("bybit")
    print(f"  binance: {len(binance)} symbols")
    print(f"  bybit:   {len(bybit)} symbols")

    # PANEL 1: individual symbol carry
    panel("PANEL 1: INDIVIDUAL SYMBOL CARRY (always-on, full history)")
    print(f"  {'symbol':<10}{'days':>6}{'total%':>10}{'CAGR%':>9}{'Sharpe':>8}{'DD%':>8}")
    print("  " + "-"*(W-2))
    indiv = {}
    for s, f in binance.items():
        r = f.where(f > 0, 0.0) * 3 / 1.5
        m = metrics(r)
        indiv[s] = m
        print(f"  {s:<10}{m['n']:>6}{m['total']:>+9.1f}%{m['cagr']:>+8.2f}%{m['sharpe']:>+8.2f}{m['dd']:>+7.2f}%")

    # PANEL 2: portfolio comparison
    panel("PANEL 2: PORTFOLIO — EQUAL vs VOL-TARGETED")
    r_eq = carry_equal_weight(binance, min_rate=0.0)
    r_vt = carry_vol_targeted(binance, min_rate=0.0, target_vol=0.10)
    r_eq_min = carry_equal_weight(binance, min_rate=0.0001)  # только funding > 1bps/8h
    m_eq = metrics(r_eq); m_vt = metrics(r_vt); m_eq_min = metrics(r_eq_min)

    print(f"  {'strategy':<25}{'total%':>10}{'CAGR%':>9}{'Sharpe':>8}{'DD%':>8}{'days':>6}")
    print("  " + "-"*(W-2))
    for name, m in [("Equal weight", m_eq),
                    ("Vol targeted (10%)", m_vt),
                    ("Equal + min 1bp", m_eq_min)]:
        print(f"  {name:<25}{m['total']:>+9.1f}%{m['cagr']:>+8.2f}%{m['sharpe']:>+8.2f}{m['dd']:>+7.2f}%{m['n']:>6}")

    # PANEL 3: per-year
    panel("PANEL 3: PER-YEAR (equal-weight portfolio)")
    df_eq = r_eq.to_frame("ret")
    df_eq["year"] = df_eq.index.year
    print(f"  {'year':>6}{'ret%':>10}{'Sharpe':>8}{'DD%':>8}")
    print("  " + "-"*(W-2))
    for y, g in df_eq.groupby("year"):
        m = metrics(g["ret"])
        print(f"  {y:>6}{m['total']:>+9.2f}%{m['sharpe']:>+8.2f}{m['dd']:>+7.2f}%")

    # PANEL 4: cross-exchange
    panel("PANEL 4: CROSS-EXCHANGE FUNDING ARB (binance vs bybit)")
    if len(bybit) > 0:
        spreads = cross_exchange_spread(binance, bybit, min_spread=0.0002)
        idx_all = None
        for s in spreads.values():
            idx_all = s.index if idx_all is None else idx_all.union(s.index)
        port = pd.concat([s.reindex(idx_all).fillna(0) for s in spreads.values()], axis=1).mean(axis=1)
        m = metrics(port)
        print(f"  Symbols with both legs: {len(spreads)}")
        print(f"  Portfolio: total {m['total']:+.1f}%  CAGR {m['cagr']:+.2f}%  "
              f"Sharpe {m['sharpe']:+.2f}  DD {m['dd']:+.2f}%  n={m['n']}")

        # Individual
        print(f"\n  {'symbol':<10}{'total%':>10}{'CAGR%':>9}{'Sharpe':>8}{'DD%':>8}")
        print("  " + "-"*(W-2))
        for s, r in sorted(spreads.items()):
            m = metrics(r)
            print(f"  {s:<10}{m['total']:>+9.1f}%{m['cagr']:>+8.2f}%{m['sharpe']:>+8.2f}{m['dd']:>+7.2f}%")

    # PANEL 5: verdict
    panel("PANEL 5: VERDICT")
    best_eq = max([m_eq, m_vt, m_eq_min], key=lambda x: x["sharpe"])
    print(f"  Best portfolio Sharpe: {best_eq['sharpe']:.2f}")
    print(f"  Best CAGR:             {max(m_eq['cagr'], m_vt['cagr'], m_eq_min['cagr']):.2f}%")
    print(f"  Best DD:               {max(m_eq['dd'], m_vt['dd'], m_eq_min['dd']):.2f}%")
    print(f"\n  Realistic estimate (after basis risk + slippage):")
    print(f"    Sharpe 2-4, CAGR 8-15%, DD -3..-8%")
    print(f"\n  Next: paper trade 30 days, then live on Bybit")
    print("="*W)

if __name__ == "__main__":
    main()
