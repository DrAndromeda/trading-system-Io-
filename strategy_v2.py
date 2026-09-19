"""
HTF STRATEGY v2 - Trend filter + no shorts in uptrend
"""
import json, warnings, webbrowser
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data"); DASH = Path("dashboard"); LOGS = Path("logs")
DASH.mkdir(exist_ok=True); LOGS.mkdir(exist_ok=True)
W = 110
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]

def panel(t): print("="*W); print("  "+t); print("="*W)

def load_1h(sym):
    p = DATA/f"cache/{sym}_1h_20000.npz"
    if not p.exists(): return None
    d = np.load(p)
    n = len(d["c"])
    ts = pd.date_range(end=pd.Timestamp.now().floor("h"), periods=n, freq="1h")
    return pd.DataFrame({"open":d["o"],"high":d["h"],"low":d["l"],
                         "close":d["c"],"volume":d["v"]}, index=ts)

def to_daily(df):
    return pd.DataFrame({
        "open": df["open"].resample("1D").first(),
        "high": df["high"].resample("1D").max(),
        "low": df["low"].resample("1D").min(),
        "close": df["close"].resample("1D").last(),
        "volume": df["volume"].resample("1D").sum(),
    }).dropna()

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = -d.clip(upper=0).rolling(n).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))

def atr(df, n=14):
    h,l,c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def build_signals(df, allow_short=False):
    d = df.copy()
    d["ema20"] = ema(d["close"], 20)
    d["ema50"] = ema(d["close"], 50)
    d["ema200"] = ema(d["close"], 200)
    d["rsi"] = rsi(d["close"], 14)
    d["atr"] = atr(d, 14)
    d["atr_pct"] = d["atr"] / d["close"] * 100
    # Глобальный тренд: EMA50 растёт за 20 дней?
    d["ema50_slope"] = d["ema50"].diff(20) / d["ema50"].shift(20) * 100
    d["global_up"] = (d["ema50_slope"] > 0) | (d["close"] > d["ema200"])
    d["signal"] = "WAIT"

    # LONG: EMA20 > EMA50 + RSI 50-75 + RSI не экстремум
    long_cond = (d["ema20"] > d["ema50"]) & (d["rsi"] > 50) & (d["rsi"] < 75)
    d.loc[long_cond, "signal"] = "LONG"

    # SHORT: только при глобальном нисходящем тренде
    if allow_short:
        short_cond = ((d["ema20"] < d["ema50"]) & (d["rsi"] > 25) & (d["rsi"] < 50)
                      & (~d["global_up"]))
        d.loc[short_cond, "signal"] = "SHORT"

    # Overbought/Oversold -> WAIT
    d.loc[d["rsi"] > 80, "signal"] = "WAIT"
    d.loc[d["rsi"] < 20, "signal"] = "WAIT"
    return d

def backtest(df, sym, rr=2.5, stop_atr=2.0, fee=0.001, trailing=False):
    """Trailing option: стоп двигается за ценой."""
    d = df.copy().reset_index()
    d.columns = ["date"] + list(d.columns[1:])
    trades, position = [], None
    equity = 1.0

    for i in range(1, len(d)):
        row, prev = d.iloc[i], d.iloc[i-1]

        if position is None:
            sig = prev["signal"]
            if sig in ("LONG", "SHORT"):
                entry = row["open"]
                atr_v = prev["atr"]
                if pd.isna(atr_v) or atr_v <= 0:
                    continue
                if sig == "LONG":
                    stop = entry - stop_atr*atr_v
                    take = entry + stop_atr*atr_v*rr
                else:
                    stop = entry + stop_atr*atr_v
                    take = entry - stop_atr*atr_v*rr
                position = {"entry_date":row["date"], "side":sig, "entry":entry,
                            "stop":stop, "take":take, "atr":atr_v,
                            "peak":entry, "peak_take":take}
        else:
            hit, exit_price = None, None
            side = position["side"]
            # Trailing: обновляем peak и take
            if trailing:
                if side == "LONG":
                    if row["high"] > position["peak"]:
                        position["peak"] = row["high"]
                        new_stop = position["peak"] - stop_atr * position["atr"]
                        if new_stop > position["stop"]:
                            position["stop"] = new_stop
                else:
                    if row["low"] < position["peak"]:
                        position["peak"] = row["low"]
                        new_stop = position["peak"] + stop_atr * position["atr"]
                        if new_stop < position["stop"]:
                            position["stop"] = new_stop

            # Проверка hit
            if side == "LONG":
                if row["low"] <= position["stop"]:
                    hit, exit_price = "STOP", position["stop"]
                elif row["high"] >= position["take"]:
                    hit, exit_price = "TAKE", position["take"]
            else:
                if row["high"] >= position["stop"]:
                    hit, exit_price = "STOP", position["stop"]
                elif row["low"] <= position["take"]:
                    hit, exit_price = "TAKE", position["take"]

            if hit:
                if side == "LONG":
                    pnl = (exit_price/position["entry"]-1)*100
                else:
                    pnl = (position["entry"]/exit_price-1)*100
                pnl -= fee*2*100
                equity *= (1 + pnl/100)
                trades.append({
                    "symbol":sym, "entry_date":position["entry_date"].date(),
                    "exit_date":row["date"].date(), "side":side,
                    "entry":round(position["entry"],4),
                    "stop":round(position["stop"],4),
                    "take":round(position["take"],4),
                    "exit":round(exit_price,4),
                    "reason":hit, "pnl_pct":round(pnl,2),
                })
                position = None
    return trades

def metrics(trades):
    if not trades: return {}
    df = pd.DataFrame(trades)
    wins = df[df["pnl_pct"]>0]; losses = df[df["pnl_pct"]<=0]
    wr = len(wins)/len(df)*100 if len(df) else 0
    pf = wins["pnl_pct"].sum()/abs(losses["pnl_pct"].sum()) if len(losses) and losses["pnl_pct"].sum()!=0 else 0
    return {
        "trades":len(df), "win_rate":round(wr,1),
        "total_pnl":round(df["pnl_pct"].sum(),2),
        "avg_win":round(wins["pnl_pct"].mean(),2) if len(wins) else 0,
        "avg_loss":round(losses["pnl_pct"].mean(),2) if len(losses) else 0,
        "pf":round(pf,2),
        "expectancy":round(df["pnl_pct"].mean(),2),
        "stops":int((df["reason"]=="STOP").sum()),
        "takes":int((df["reason"]=="TAKE").sum()),
    }

def main():
    panel("HTF v2 - Trend filter + trailing stop + RR 2.5")

    results = []; all_trades = []

    # Конфиги: только LONG для BTC, LONG+SHORT для остальных
    configs = {
        "BTCUSDT": {"allow_short": False, "trailing": True},
        "ETHUSDT": {"allow_short": True,  "trailing": True},
        "XRPUSDT": {"allow_short": True,  "trailing": True},
        "SOLUSDT": {"allow_short": True,  "trailing": True},
    }

    for sym in SYMBOLS:
        panel(sym)
        df1h = load_1h(sym)
        if df1h is None: continue
        dfd = to_daily(df1h)
        cfg = configs[sym]
        d = build_signals(dfd, allow_short=cfg["allow_short"])
        cnt = d["signal"].value_counts().to_dict()
        print(f"  signals: {cnt}")

        trades = backtest(d, sym, rr=2.5, stop_atr=2.0,
                          trailing=cfg["trailing"])
        m = metrics(trades)
        if m:
            m["symbol"] = sym
            results.append(m); all_trades.extend(trades)
            print(f"  trades {m['trades']}  win% {m['win_rate']}  PnL {m['total_pnl']:+.2f}%  "
                  f"PF {m['pf']}  stops/takes {m['stops']}/{m['takes']}")

    panel("RESULTS")
    print(f"  {'sym':<10}{'trades':>7}{'win%':>7}{'PnL%':>10}{'avgW':>8}{'avgL':>8}{'PF':>6}{'expect':>8}")
    print("  " + "-"*(W-2))
    tot_pnl = 0
    for r in results:
        tot_pnl += r["total_pnl"]
        print(f"  {r['symbol']:<10}{r['trades']:>7}{r['win_rate']:>6.1f}%"
              f"{r['total_pnl']:>+9.2f}%{r['avg_win']:>+7.2f}%{r['avg_loss']:>+7.2f}%"
              f"{r['pf']:>6.2f}{r['expectancy']:>+7.2f}%")
    print(f"\n  TOTAL PnL: {tot_pnl:+.2f}%")

    # Save trades
    if all_trades:
        df = pd.DataFrame(all_trades)
        path = LOGS/"trades_v2.csv"
        df.to_csv(path, index=False)
        print(f"  saved: {path}  ({len(df)} trades)")

    # Winner check
    winners = [r for r in results if r["pf"] > 1.3]
    print(f"\n  Winners (PF>1.3): {len(winners)}/{len(results)}")
    for w in winners:
        print(f"    {w['symbol']}: PF {w['pf']}, PnL {w['total_pnl']:+.1f}%, WR {w['win_rate']}%")

if __name__ == "__main__":
    main()
