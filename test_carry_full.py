import json, warnings
import numpy as np, pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")
DATA = Path("data/onchain")
DOCS = Path("docs"); DOCS.mkdir(exist_ok=True)
W = 100

def panel(t): print("="*W); print("  "+t); print("="*W)

def load_blockchain():
    d = json.loads((DATA/"blockchain_info.json").read_text())
    df = pd.DataFrame(d["price"]).rename(columns={"value":"price","ts":"ts"})
    for k in ["hashrate","active_addresses"]:
        t = pd.DataFrame(d[k]).rename(columns={"value":k})
        df = pd.merge(df, t, on="ts", how="outer")
    df = df.sort_values("ts").reset_index(drop=True).ffill().dropna()
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def synth_funding(n, seed=42):
    rng = np.random.default_rng(seed)
    base = 0.0001
    noise = rng.normal(0, 0.0003, n)
    spikes = rng.choice([0,0,0,0,0,1], n) * rng.normal(0.001, 0.001, n)
    f = base + noise + spikes
    return np.clip(f, -0.003, 0.003)

def rule_signal(df):
    p = df["price"]; h = df["hashrate"]
    return ((p > p.rolling(30).mean()) & (h > h.rolling(30).mean())).astype(int)

def metrics(ret, pos, fee=0.001):
    ret = np.asarray(ret); pos = np.asarray(pos, float)
    turn = np.abs(np.diff(pos, prepend=0.0))
    eq = pos[:-1]*ret[1:] - turn[1:]*fee
    if len(eq)==0: return None
    cum = np.cumprod(1+eq)
    total = (cum[-1]-1)*100
    dd = ((cum/np.maximum.accumulate(cum))-1).min()*100
    expo = (pos!=0).mean()*100
    sh = (eq.mean()/eq.std()*np.sqrt(365)) if eq.std()>0 else 0.0
    return dict(total=total, dd=dd, exposure=expo, sharpe=sh)

def run_all(df):
    ret = df["price"].pct_change().fillna(0).values
    sig = rule_signal(df).shift(1).fillna(0).values
    funding = synth_funding(len(df))
    m_rule = metrics(ret, sig)
    carry_ret = funding * 3
    eq_carry = np.cumprod(1 + carry_ret[1:])
    cum_carry = np.concatenate([[1.0], eq_carry])
    total_carry = (cum_carry[-1]-1)*100
    dd_carry = ((cum_carry/np.maximum.accumulate(cum_carry))-1).min()*100
    sh_carry = (carry_ret[1:].mean()/carry_ret[1:].std()*np.sqrt(365)) if carry_ret[1:].std()>0 else 0.0
    m_carry = dict(total=total_carry, dd=dd_carry, exposure=100.0, sharpe=sh_carry)
    pos_hybrid = sig
    hybrid_ret = pos_hybrid[:-1] * carry_ret[1:]
    turn_h = np.abs(np.diff(pos_hybrid, prepend=0.0))
    hybrid_ret = hybrid_ret - turn_h[1:]*0.001
    cum_h = np.cumprod(1 + hybrid_ret)
    total_h = (cum_h[-1]-1)*100
    dd_h = ((cum_h/np.maximum.accumulate(cum_h))-1).min()*100
    sh_h = (hybrid_ret.mean()/hybrid_ret.std()*np.sqrt(365)) if hybrid_ret.std()>0 else 0.0
    expo_h = (pos_hybrid!=0).mean()*100
    m_hybrid = dict(total=total_h, dd=dd_h, exposure=expo_h, sharpe=sh_h)
    return m_rule, m_carry, m_hybrid

def multi_period(df, ks=(2,3,4)):
    rows = []
    for k in ks:
        n = len(df)//k
        for i in range(k):
            part = df.iloc[i*n:(i+1)*n].reset_index(drop=True)
            if len(part) < 60: continue
            mr, mc, mh = run_all(part)
            bh = (part["price"].iloc[-1]/part["price"].iloc[0]-1)*100
            rows.append(dict(k=k, i=i+1,
                             start=part["date"].iloc[0].date(),
                             end=part["date"].iloc[-1].date(),
                             bh=bh,
                             rule_total=mr["total"], rule_alpha=mr["total"]-bh, rule_sh=mr["sharpe"],
                             carry_total=mc["total"], carry_sh=mc["sharpe"],
                             hybrid_total=mh["total"], hybrid_alpha=mh["total"]-bh, hybrid_sh=mh["sharpe"]))
    return pd.DataFrame(rows)

def write_docs(df_raw, tbl):
    L = []
    L.append("# Strategy Documentation — on-chain + funding carry\n\n")
    L.append("Auto-generated. Do not edit manually.\n\n")
    L.append("## Data\n\n")
    L.append(f"- Range: {df_raw['date'].iloc[0].date()} -> {df_raw['date'].iloc[-1].date()}\n")
    L.append(f"- Bars: {len(df_raw)}\n\n")
    L.append("## Strategies\n\n")
    L.append("1. Rule (directional): long when price>SMA30 AND hashrate>SMA30\n")
    L.append("2. Carry (delta-neutral): long spot + short perp, capture funding\n")
    L.append("3. Hybrid: carry only when rule==1 (regime filter)\n\n")
    L.append("## Multi-period results\n\n")
    L.append("| K | i | start | end | BH% | rule_tot% | rule_a% | rule_sh | carry_tot% | carry_sh | hyb_tot% | hyb_a% | hyb_sh |\n")
    L.append("|---|---|-------|-----|-----|-----------|---------|---------|------------|----------|----------|--------|--------|\n")
    for _, r in tbl.iterrows():
        L.append(f"| {int(r['k'])} | {int(r['i'])} | {r['start']} | {r['end']} | "
                 f"{r['bh']:+.1f} | {r['rule_total']:+.1f} | {r['rule_alpha']:+.1f} | {r['rule_sh']:+.2f} | "
                 f"{r['carry_total']:+.1f} | {r['carry_sh']:+.2f} | "
                 f"{r['hybrid_total']:+.1f} | {r['hybrid_alpha']:+.1f} | {r['hybrid_sh']:+.2f} |\n")
    r_avg = tbl['rule_alpha'].mean(); h_avg = tbl['hybrid_alpha'].mean()
    L.append("\n## Verdict\n\n")
    L.append(f"- Rule avg alpha: {r_avg:+.1f}%\n")
    L.append(f"- Hybrid avg alpha: {h_avg:+.1f}%\n")
    L.append(f"- Winner: {'HYBRID' if h_avg > r_avg else 'RULE'}\n\n")
    L.append("## Notes\n\n")
    L.append("- Funding series synthesized with fixed seed for reproducibility\n")
    L.append("- Replace synth_funding() with real exchange data for production\n")
    L.append("- Fee assumption: 0.1% per rebalance\n")
    txt = "".join(L)
    (DOCS/"STRATEGY.md").write_text(txt)
    return txt

def main():
    panel("ON-CHAIN + FUNDING CARRY — FULL TEST")
    df = load_blockchain()
    print(f"  Data: {len(df)} days ({df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()})")

    panel("PANEL 1: FULL PERIOD")
    mr, mc, mh = run_all(df)
    bh = (df["price"].iloc[-1]/df["price"].iloc[0]-1)*100
    print(f"  Buy & Hold:        {bh:+.1f}%")
    print(f"  Rule (long-only):  {mr['total']:+.1f}%  DD {mr['dd']:+.1f}%  Sharpe {mr['sharpe']:+.2f}  expo {mr['exposure']:.0f}%")
    print(f"  Carry (neutral):   {mc['total']:+.1f}%  DD {mc['dd']:+.1f}%  Sharpe {mc['sharpe']:+.2f}  expo {mc['exposure']:.0f}%")
    print(f"  Hybrid (carry+flt):{mh['total']:+.1f}%  DD {mh['dd']:+.1f}%  Sharpe {mh['sharpe']:+.2f}  expo {mh['exposure']:.0f}%")

    panel("PANEL 2: MULTI-PERIOD (2/3/4)")
    tbl = multi_period(df, (2,3,4))
    print(f"  {'K':>2}{'i':>3}{'start':>12}{'end':>12}{'BH%':>9}{'Rule a%':>10}{'RuleSh':>8}{'Carry%':>9}{'CarrySh':>9}{'Hyb a%':>9}{'HybSh':>8}")
    print("  "+"-"*(W-2))
    for _, r in tbl.iterrows():
        print(f"  {int(r['k']):>2}{int(r['i']):>3}{str(r['start']):>12}{str(r['end']):>12}"
              f"{r['bh']:>+8.1f}%{r['rule_alpha']:>+9.1f}%{r['rule_sh']:>8.2f}"
              f"{r['carry_total']:>+8.1f}%{r['carry_sh']:>9.2f}{r['hybrid_alpha']:>+8.1f}%{r['hybrid_sh']:>8.2f}")

    panel("PANEL 3: SUMMARY")
    print(f"  Rule   wins: {(tbl['rule_alpha']>0).sum()}/{len(tbl)}  avg alpha {tbl['rule_alpha'].mean():+.1f}%")
    print(f"  Hybrid wins: {(tbl['hybrid_alpha']>0).sum()}/{len(tbl)}  avg alpha {tbl['hybrid_alpha'].mean():+.1f}%")
    print(f"  Carry  wins: {(tbl['carry_total']>0).sum()}/{len(tbl)}  avg total {tbl['carry_total'].mean():+.1f}%")

    panel("PANEL 4: VERDICT")
    r_avg = tbl['rule_alpha'].mean(); h_avg = tbl['hybrid_alpha'].mean()
    if h_avg > r_avg and h_avg > 0:
        print("  WINNER: HYBRID (carry + rule filter)")
    elif r_avg > 0:
        print("  WINNER: RULE (directional)")
    else:
        print("  NO WINNER: both negative")

    panel("DOCS WRITTEN")
    txt = write_docs(df, tbl)
    print(f"  Written: docs/STRATEGY.md")
    print(f"  Size: {len(txt)} chars")
    print("="*W)
    print("  DONE. See docs/STRATEGY.md")
    print("="*W)

if __name__ == "__main__":
    main()
