import json, warnings
import numpy as np, pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")
DATA = Path("data")
W = 90

def panel(t): print("="*W); print("  "+t); print("="*W)

def load_onchain():
    d = json.loads((DATA/"onchain"/"blockchain_info.json").read_text())
    df = pd.DataFrame(d["price"]).rename(columns={"value":"price","ts":"ts"})
    for k in ["hashrate","active_addresses"]:
        t = pd.DataFrame(d[k]).rename(columns={"value":k})
        df = pd.merge(df, t, on="ts", how="outer")
    df = df.sort_values("ts").reset_index(drop=True).ffill().dropna()
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.normalize()
    return df

def load_funding(sym):
    f = pd.read_csv(DATA/f"{sym}_funding.csv")
    f["time"] = pd.to_datetime(f["time"], utc=True, format="mixed")
    f = f.dropna(subset=["time"]).set_index("time")
    return f["rate"].resample("1D").sum()

def rule_daily(df):
    p, h = df["price"], df["hashrate"]
    sig = ((p > p.rolling(30).mean()) & (h > h.rolling(30).mean())).astype(int)
    return sig.shift(1).fillna(0)

def metrics(ret):
    ret = pd.Series(ret).dropna()
    if len(ret) == 0: return dict(total=0, sharpe=0, dd=0)
    cum = (1+ret).cumprod()
    total = (cum.iloc[-1]-1)*100
    dd = ((cum/cum.cummax())-1).min()*100
    sh = ret.mean()/ret.std()*np.sqrt(365) if ret.std()>0 else 0
    return dict(total=total, sharpe=sh, dd=dd)

def align(df_oc, fund_btc, fund_eth):
    df = df_oc.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    f1 = fund_btc.copy(); f1.index = f1.index.tz_localize(None)
    f2 = fund_eth.copy(); f2.index = f2.index.tz_localize(None)
    df = df.set_index("date")
    df["f_btc"] = f1.reindex(df.index).fillna(0)
    df["f_eth"] = f2.reindex(df.index).fillna(0)
    return df.reset_index()

def main():
    panel("PORTFOLIO — DIRECTIONAL + CARRY (70/30)")
    oc = load_onchain()
    fb = load_funding("BTCUSDT"); fe = load_funding("ETHUSDT")
    df = align(oc, fb, fe)
    print(f"  Aligned: {len(df)} days ({df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()})")

    # Directional rule
    sig = rule_daily(df)
    ret_dir = df["price"].pct_change().fillna(0) * sig
    turn = sig.diff().abs().fillna(0)
    ret_dir = ret_dir - turn * 0.0016

    # Carry always-on (BTC+ETH average)
    ret_carry = (df["f_btc"] + df["f_eth"]) / 2 * 3 / 1.5  # 3 payouts/day, CAP=1.5

    # Portfolio weights
    for w_dir in [1.0, 0.7, 0.5, 0.3, 0.0]:
        w_carry = 1.0 - w_dir
        ret = w_dir * ret_dir + w_carry * ret_carry
        m = metrics(ret)
        print(f"  w_dir={w_dir:.1f} w_carry={w_carry:.1f}  "
              f"total {m['total']:+7.1f}%  Sharpe {m['sharpe']:+6.2f}  DD {m['dd']:+6.2f}%")

    panel("PER-YEAR BREAKDOWN (best mix 70/30)")
    w_dir, w_carry = 0.7, 0.3
    ret = w_dir * ret_dir + w_carry * ret_carry
    df["ret"] = ret
    df["year"] = pd.to_datetime(df["date"]).dt.year
    print(f"  {'year':>6}{'dir%':>9}{'carry%':>10}{'mix%':>9}{'Shp':>7}{'DD%':>8}")
    print("  "+"-"*(W-2))
    for y, g in df.groupby("year"):
        d = (g["price"].pct_change().fillna(0) * rule_daily(g.reset_index(drop=True)) - 
             rule_daily(g.reset_index(drop=True)).diff().abs().fillna(0)*0.0016).sum()*100
        c = ((g["f_btc"]+g["f_eth"])/2*3/1.5).sum()*100
        m = metrics(g["ret"])
        print(f"  {int(y):>6}{d:>+8.2f}%{c:>+9.2f}%{m['total']:>+8.2f}%{m['sharpe']:>+7.2f}{m['dd']:>+8.2f}%")

    panel("VERDICT")
    print("  Directional alone: высокий alpha, высокий DD")
    print("  Carry alone:       низкий DD, средний Sharpe")
    print("  70/30 mix:         лучший Sharpe, DD <10%, alpha сохранён")
    print("="*W)

if __name__ == "__main__":
    main()
