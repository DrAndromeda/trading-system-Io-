"""
HTF STRATEGY - Daily/Weekly signals + SL/TP backtest
=====================================================
1. Resample 1h -> 1D and 1W
2. Signals on 1D: EMA cross + RSI + ATR
3. Backtest with entry / stop / take
4. Log all signals to signals_log.csv
5. Log all trades to trades_log.csv
6. Compare vs Buy&Hold
7. Dashboard with signal table
"""
import json, os, warnings, webbrowser
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data")
DASH = Path("dashboard"); DASH.mkdir(exist_ok=True)
LOGS = Path("logs"); LOGS.mkdir(exist_ok=True)
W = 110
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]

def panel(t): print("="*W); print("  "+t); print("="*W)

# ---------- DATA ----------
def load_1h(sym):
    p = DATA/f"cache/{sym}_1h_20000.npz"
    if not p.exists(): return None
    d = np.load(p)
    n = len(d["c"])
    ts = pd.date_range(end=pd.Timestamp.now().floor("h"), periods=n, freq="1h")
    return pd.DataFrame({
        "open": d["o"], "high": d["h"], "low": d["l"],
        "close": d["c"], "volume": d["v"],
    }, index=ts)

def to_daily(df):
    o = df["open"].resample("1D").first()
    h = df["high"].resample("1D").max()
    l = df["low"].resample("1D").min()
    c = df["close"].resample("1D").last()
    v = df["volume"].resample("1D").sum()
    return pd.DataFrame({"open":o,"high":h,"low":l,"close":c,"volume":v}).dropna()

def to_weekly(df):
    o = df["open"].resample("1W").first()
    h = df["high"].resample("1W").max()
    l = df["low"].resample("1W").min()
    c = df["close"].resample("1W").last()
    v = df["volume"].resample("1W").sum()
    return pd.DataFrame({"open":o,"high":h,"low":l,"close":c,"volume":v}).dropna()

# ---------- INDICATORS ----------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = -d.clip(upper=0).rolling(n).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - 100/(1+rs)

def atr(df, n=14):
    h,l,c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# ---------- SIGNALS ----------
def build_signals(df):
    d = df.copy()
    d["ema20"] = ema(d["close"], 20)
    d["ema50"] = ema(d["close"], 50)
    d["rsi"] = rsi(d["close"], 14)
    d["atr"] = atr(d, 14)
    d["atr_pct"] = d["atr"] / d["close"] * 100
    d["trend"] = np.where(d["ema20"] > d["ema50"], 1, -1)
    d["signal"] = "WAIT"
    # LONG: тренд вверх + RSI 50-75 (не перекуплен)
    long_cond = (d["trend"] == 1) & (d["rsi"] > 50) & (d["rsi"] < 75)
    # SHORT: тренд вниз + RSI 25-50
    short_cond = (d["trend"] == -1) & (d["rsi"] > 25) & (d["rsi"] < 50)
    d.loc[long_cond, "signal"] = "LONG"
    d.loc[short_cond, "signal"] = "SHORT"
    # Overbought -> WAIT даже если тренд
    d.loc[d["rsi"] > 80, "signal"] = "WAIT"
    d.loc[d["rsi"] < 20, "signal"] = "WAIT"
    return d

# ---------- BACKTEST WITH SL/TP ----------
def backtest(df, sym, rr=2.0, stop_atr=2.0, fee=0.001):
    """
    Идём по барам:
    - сигнал на закрытии бара i
    - вход на открытии бара i+1
    - стоп и тейк проверяются внутри бара
    """
    d = df.copy().reset_index()
    d.columns = ["date"] + list(d.columns[1:])
    trades = []
    position = None
    equity = 1.0
    eq_curve = [1.0]

    for i in range(1, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i-1]

        if position is None:
            sig = prev["signal"]
            if sig in ("LONG", "SHORT"):
                entry = row["open"]
                atr_v = prev["atr"]
                if pd.isna(atr_v) or atr_v <= 0:
                    eq_curve.append(equity); continue
                if sig == "LONG":
                    stop = entry - stop_atr * atr_v
                    take = entry + stop_atr * atr_v * rr
                else:
                    stop = entry + stop_atr * atr_v
                    take = entry - stop_atr * atr_v * rr
                position = {
                    "entry_date": row["date"], "side": sig,
                    "entry": entry, "stop": stop, "take": take,
                    "atr": atr_v,
                }
            eq_curve.append(equity)
        else:
            # проверяем стоп/тейк внутри бара
            hit = None
            if position["side"] == "LONG":
                if row["low"] <= position["stop"]:
                    hit = "STOP"; exit_price = position["stop"]
                elif row["high"] >= position["take"]:
                    hit = "TAKE"; exit_price = position["take"]
            else:
                if row["high"] >= position["stop"]:
                    hit = "STOP"; exit_price = position["stop"]
                elif row["low"] <= position["take"]:
                    hit = "TAKE"; exit_price = position["take"]

            if hit:
                if position["side"] == "LONG":
                    pnl_pct = (exit_price / position["entry"] - 1) * 100
                else:
                    pnl_pct = (position["entry"] / exit_price - 1) * 100
                pnl_pct -= fee * 2 * 100
                equity *= (1 + pnl_pct / 100)
                trades.append({
                    "symbol": sym,
                    "entry_date": position["entry_date"].date(),
                    "exit_date": row["date"].date(),
                    "side": position["side"],
                    "entry": round(position["entry"], 4),
                    "stop": round(position["stop"], 4),
                    "take": round(position["take"], 4),
                    "exit": round(exit_price, 4),
                    "exit_reason": hit,
                    "pnl_pct": round(pnl_pct, 2),
                    "atr": round(position["atr"], 4),
                })
                position = None
            eq_curve.append(equity)

    return trades, eq_curve

# ---------- METRICS ----------
def trade_metrics(trades):
    if not trades: return {}
    df = pd.DataFrame(trades)
    wins = df[df["pnl_pct"] > 0]
    losses = df[df["pnl_pct"] <= 0]
    total = df["pnl_pct"].sum()
    wr = len(wins)/len(df)*100 if len(df) else 0
    avg_w = wins["pnl_pct"].mean() if len(wins) else 0
    avg_l = losses["pnl_pct"].mean() if len(losses) else 0
    pf = wins["pnl_pct"].sum() / abs(losses["pnl_pct"].sum()) if len(losses) and losses["pnl_pct"].sum() != 0 else 0
    expectancy = df["pnl_pct"].mean() if len(df) else 0
    return {
        "trades": len(df), "win_rate": round(wr, 1),
        "total_pnl_pct": round(total, 2),
        "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
        "profit_factor": round(pf, 2), "expectancy": round(expectancy, 2),
        "stops": int((df["exit_reason"]=="STOP").sum()),
        "takes": int((df["exit_reason"]=="TAKE").sum()),
    }

# ---------- LOGS ----------
def save_signals(sym, df):
    """Все дневные сигналы в CSV."""
    out = df.copy()
    out["symbol"] = sym
    out["date"] = out.index
    cols = ["date","symbol","close","ema20","ema50","rsi","atr","atr_pct","trend","signal"]
    out = out[cols]
    path = LOGS/f"signals_{sym}.csv"
    out.to_csv(path, index=False)
    return path

def save_trades_all(all_trades):
    if not all_trades: return None
    df = pd.DataFrame(all_trades)
    path = LOGS/"trades_log.csv"
    df.to_csv(path, index=False)
    return path

# ---------- DASHBOARD ----------
def make_dashboard(results, prices, latest):
    rows = ""
    for r in results:
        rows += f'''<tr>
          <td><b>{r['symbol']}</b></td>
          <td>{r['trades']}</td>
          <td class="{'pos' if r['win_rate']>50 else 'neg'}">{r['win_rate']}%</td>
          <td class="{'pos' if r['total_pnl_pct']>0 else 'neg'}">{r['total_pnl_pct']:+.2f}%</td>
          <td>{r['avg_win']:+.2f}%</td>
          <td>{r['avg_loss']:+.2f}%</td>
          <td class="{'pos' if r['profit_factor']>1 else 'neg'}">{r['profit_factor']}</td>
          <td>{r['expectancy']:+.2f}%</td>
          <td>{r['stops']}</td>
          <td>{r['takes']}</td>
        </tr>'''

    latest_rows = ""
    for L in latest:
        cls = "pos" if L["signal"]=="LONG" else "neg" if L["signal"]=="SHORT" else ""
        latest_rows += f'''<tr>
          <td><b>{L['symbol']}</b></td>
          <td>{L['date']}</td>
          <td>${L['close']}</td>
          <td>{L['ema20']}</td>
          <td>{L['ema50']}</td>
          <td>{L['rsi']}</td>
          <td>{L['atr_pct']}%</td>
          <td class="{cls}"><b>{L['signal']}</b></td>
          <td>{L['entry']}</td>
          <td class="neg">{L['stop']}</td>
          <td class="pos">{L['take']}</td>
        </tr>'''

    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>HTF Strategy</title>
<style>
*{box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;margin:0;padding:20px}
h1{color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px}
h2{color:#58a6ff;margin-top:35px}
.time{color:#8b949e;font-size:13px;margin-bottom:15px}
table{width:100%;border-collapse:collapse;margin:15px 0;font-size:14px}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #21262d}
th{color:#8b949e;font-size:11px;text-transform:uppercase;background:#161b22}
tr:hover{background:#161b22}
.pos{color:#3fb950}.neg{color:#f85149}
.info{background:#161b22;border-left:3px solid #58a6ff;padding:15px;border-radius:6px;margin:15px 0}
.info b{color:#58a6ff}
</style></head>
<body>
<h1>HTF Strategy - Daily Signals</h1>
<div class="time">Generated: __NOW__</div>
<div class="info">
<b>Rules:</b> EMA20 vs EMA50 trend + RSI filter | Entry on next open | SL = 2xATR | TP = 2xATR x RR(2.0) | Fee 0.1%
</div>
<h2>Latest Signals</h2>
<table>
<tr><th>Symbol</th><th>Date</th><th>Close</th><th>EMA20</th><th>EMA50</th><th>RSI</th><th>ATR%</th><th>Signal</th><th>Entry</th><th>Stop</th><th>Take</th></tr>
__LATEST__
</table>
<h2>Backtest Results</h2>
<table>
<tr><th>Symbol</th><th>Trades</th><th>Win Rate</th><th>Total PnL</th><th>Avg Win</th><th>Avg Loss</th><th>Profit Factor</th><th>Expectancy</th><th>Stops</th><th>Takes</th></tr>
__RESULTS__
</table>
</body></html>"""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = html.replace("__LATEST__", latest_rows).replace("__RESULTS__", rows).replace("__NOW__", now)
    path = DASH/"htf_strategy.html"
    path.write_text(html)
    return path

# ---------- MAIN ----------
def main():
    panel("HTF STRATEGY - Daily signals + SL/TP backtest")

    results = []
    latest = []
    all_trades = []

    for sym in SYMBOLS:
        panel(f"{sym}")
        df1h = load_1h(sym)
        if df1h is None:
            print("  no data"); continue
        dfd = to_daily(df1h)
        dfw = to_weekly(df1h)
        print(f"  1h: {len(df1h)}  |  1D: {len(dfd)}  |  1W: {len(dfw)}")

        d = build_signals(dfd)
        sig_path = save_signals(sym, d)
        print(f"  signals: {sig_path}")

        # Latest
        last = d.iloc[-1]
        sig_cnt = d["signal"].value_counts().to_dict()
        print(f"  signal counts: {sig_cnt}")
        cur = last["signal"]
        if cur in ("LONG","SHORT") and not pd.isna(last["atr"]):
            entry = round(float(last["close"]), 4)
            atr_v = float(last["atr"])
            if cur == "LONG":
                stop = round(entry - 2*atr_v, 4); take = round(entry + 4*atr_v, 4)
            else:
                stop = round(entry + 2*atr_v, 4); take = round(entry - 4*atr_v, 4)
            latest.append({
                "symbol": sym, "date": str(last.name.date()), "close": entry,
                "ema20": round(float(last["ema20"]),4), "ema50": round(float(last["ema50"]),4),
                "rsi": round(float(last["rsi"]),1), "atr_pct": round(float(last["atr_pct"]),2),
                "signal": cur, "entry": entry, "stop": stop, "take": take,
            })

        # Backtest
        trades, eq = backtest(d, sym, rr=2.0, stop_atr=2.0)
        m = trade_metrics(trades)
        if m:
            m["symbol"] = sym
            results.append(m)
            all_trades.extend(trades)
            print(f"  trades: {m['trades']}  win%: {m['win_rate']}  PnL: {m['total_pnl_pct']:+.2f}%  PF: {m['profit_factor']}")

    panel("RESULTS SUMMARY")
    print(f"  {'sym':<10}{'trades':>7}{'win%':>7}{'PnL%':>10}{'avgW':>8}{'avgL':>8}{'PF':>6}{'expect':>8}")
    print("  " + "-"*(W-2))
    for r in results:
        print(f"  {r['symbol']:<10}{r['trades']:>7}{r['win_rate']:>6.1f}%"
              f"{r['total_pnl_pct']:>+9.2f}%{r['avg_win']:>+7.2f}%{r['avg_loss']:>+7.2f}%"
              f"{r['profit_factor']:>6.2f}{r['expectancy']:>+7.2f}%")

    panel("LATEST ACTIVE SIGNALS")
    for L in latest:
        print(f"  {L['symbol']:<10} {L['signal']:<6} entry={L['entry']}  SL={L['stop']}  TP={L['take']}  RSI={L['rsi']}")

    panel("SAVING LOGS")
    tp = save_trades_all(all_trades)
    print(f"  trades: {tp}")
    print(f"  signals in: {LOGS}/signals_*.csv")

    panel("DASHBOARD")
    path = make_dashboard(results, None, latest)
    print(f"  {path}")

    panel("TELEGRAM")
    if TG_TOKEN and TG_CHAT:
        lines = ["<b>HTF Signals</b>", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),""]
        for L in latest:
            lines.append(f"<b>{L['symbol']}</b> {L['signal']} @ {L['entry']}")
            lines.append(f"  SL: {L['stop']}  TP: {L['take']}")
        try:
            from urllib.request import urlopen, Request
            from urllib.parse import urlencode
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            data = urlencode({"chat_id": TG_CHAT, "text": "\n".join(lines), "parse_mode":"HTML"}).encode()
            with urlopen(Request(url, data=data), timeout=10) as r:
                print(f"  sent: {r.status == 200}")
        except Exception as e:
            print(f"  err: {e}")
    else:
        print("  dry-run (no token)")

    panel("DONE")
    webbrowser.open(f"file://{path.resolve()}")
    print(f"  Reports: {LOGS}/")
    print(f"  Dashboard: {path}")
    print("="*W)

if __name__ == "__main__":
    main()
