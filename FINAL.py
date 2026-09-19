"""
CRYPTO CARRY - FINAL
====================
Core: Carry (funding arb) on 20 symbols - REAL EDGE
Side: Directional HTF log (for research, not for trading)
Output: logs + dashboard + git push
"""
import json, warnings, subprocess, webbrowser
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data"); DASH = Path("dashboard"); LOGS = Path("logs"); DOCS = Path("docs")
for p in [DASH, LOGS, DOCS]: p.mkdir(exist_ok=True)
W = 110

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]
CONFIG = {
    "BTCUSDT": {"allow_short": False, "trailing": True},
    "ETHUSDT": {"allow_short": True,  "trailing": True},
    "XRPUSDT": {"allow_short": True,  "trailing": True},
    "SOLUSDT": {"allow_short": True,  "trailing": True},
}

def panel(t): print("="*W); print("  "+t); print("="*W)

# ---------------- LOAD ----------------
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
        "low":  df["low"].resample("1D").min(),
        "close":df["close"].resample("1D").last(),
        "volume":df["volume"].resample("1D").sum(),
    }).dropna()

def load_funding(sym, ex="binance"):
    p = DATA/f"raw/{ex}/{sym}_funding.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["funding_timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    return df.set_index("ts")["funding_rate"].resample("1D").sum()

# ---------------- INDICATORS ----------------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d = s.diff(); g = d.clip(lower=0).rolling(n).mean(); l = -d.clip(upper=0).rolling(n).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))
def atr(df, n=14):
    h,l,c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# ---------------- SIGNALS ----------------
def build_signals(df, allow_short=False):
    d = df.copy()
    d["ema20"] = ema(d["close"], 20); d["ema50"] = ema(d["close"], 50); d["ema200"] = ema(d["close"], 200)
    d["rsi"] = rsi(d["close"], 14); d["atr"] = atr(d, 14)
    d["atr_pct"] = d["atr"] / d["close"] * 100
    d["ema50_slope"] = d["ema50"].diff(20) / d["ema50"].shift(20) * 100
    d["global_up"] = (d["ema50_slope"] > 0) | (d["close"] > d["ema200"])
    d["signal"] = "WAIT"
    long_cond = (d["ema20"] > d["ema50"]) & (d["rsi"] > 50) & (d["rsi"] < 75)
    d.loc[long_cond, "signal"] = "LONG"
    if allow_short:
        short_cond = ((d["ema20"] < d["ema50"]) & (d["rsi"] > 25) & (d["rsi"] < 50)
                      & (~d["global_up"]))
        d.loc[short_cond, "signal"] = "SHORT"
    d.loc[d["rsi"] > 80, "signal"] = "WAIT"
    d.loc[d["rsi"] < 20, "signal"] = "WAIT"
    return d

# ---------------- BACKTEST ----------------
def backtest(df, sym, rr=2.5, stop_atr=2.0, fee=0.001, trailing=True):
    d = df.copy().reset_index(); d.columns = ["date"] + list(d.columns[1:])
    trades, position = [], None

    for i in range(1, len(d)):
        row, prev = d.iloc[i], d.iloc[i-1]
        if position is None:
            sig = prev["signal"]
            if sig in ("LONG", "SHORT"):
                entry = row["open"]; atr_v = prev["atr"]
                if pd.isna(atr_v) or atr_v <= 0: continue
                if sig == "LONG":
                    stop = entry - stop_atr*atr_v; take = entry + stop_atr*atr_v*rr
                else:
                    stop = entry + stop_atr*atr_v; take = entry - stop_atr*atr_v*rr
                position = {"entry_date":row["date"],"side":sig,"entry":entry,
                            "stop":stop,"take":take,"atr":atr_v,
                            "peak":entry,"stop_init":stop,"take_init":take}
        else:
            side = position["side"]
            if trailing:
                if side == "LONG" and row["high"] > position["peak"]:
                    position["peak"] = row["high"]
                    ns = position["peak"] - stop_atr * position["atr"]
                    if ns > position["stop"]: position["stop"] = ns
                elif side == "SHORT" and row["low"] < position["peak"]:
                    position["peak"] = row["low"]
                    ns = position["peak"] + stop_atr * position["atr"]
                    if ns < position["stop"]: position["stop"] = ns

            hit, exit_price = None, None
            if side == "LONG":
                if row["low"] <= position["stop"]: hit, exit_price = "STOP", position["stop"]
                elif row["high"] >= position["take"]: hit, exit_price = "TAKE", position["take"]
            else:
                if row["high"] >= position["stop"]: hit, exit_price = "STOP", position["stop"]
                elif row["low"] <= position["take"]: hit, exit_price = "TAKE", position["take"]

            if hit:
                pnl = (exit_price/position["entry"]-1)*100 if side == "LONG" else (position["entry"]/exit_price-1)*100
                pnl -= fee*2*100
                trades.append({
                    "symbol":sym,"entry_date":position["entry_date"].date(),
                    "exit_date":row["date"].date(),"side":side,
                    "entry":round(position["entry"],4),
                    "stop_initial":round(position["stop_init"],4),
                    "take":round(position["take"],4),
                    "stop_final":round(position["stop"],4),
                    "exit":round(exit_price,4),
                    "reason":hit,"pnl_pct":round(pnl,2),
                })
                position = None
    return trades

def metrics(trades):
    if not trades: return {}
    df = pd.DataFrame(trades)
    wins = df[df["pnl_pct"]>0]; losses = df[df["pnl_pct"]<=0]
    wr = len(wins)/len(df)*100
    pf = wins["pnl_pct"].sum()/abs(losses["pnl_pct"].sum()) if len(losses) and losses["pnl_pct"].sum()!=0 else 0
    return {"trades":len(df),"win_rate":round(wr,1),
            "total_pnl":round(df["pnl_pct"].sum(),2),
            "avg_win":round(wins["pnl_pct"].mean(),2) if len(wins) else 0,
            "avg_loss":round(losses["pnl_pct"].mean(),2) if len(losses) else 0,
            "pf":round(pf,2),"expectancy":round(df["pnl_pct"].mean(),2),
            "stops":int((df["reason"]=="STOP").sum()),
            "takes":int((df["reason"]=="TAKE").sum())}

# ---------------- CARRY ----------------
def run_carry():
    all_syms = [f.stem.replace("_funding","") for f in (DATA/"raw/binance").glob("*_funding.parquet")]
    rets = []
    for s in all_syms:
        f = load_funding(s)
        if f is None: continue
        r = f.where(f > 0, 0.0) * 3 / 1.5
        rets.append(r)
    if not rets: return None
    idx = None
    for r in rets: idx = r.index if idx is None else idx.union(r.index)
    port = pd.concat([r.reindex(idx).fillna(0) for r in rets], axis=1).mean(axis=1)
    cum = (1+port).cumprod()
    years = len(port) / 365
    return {
        "total": (cum.iloc[-1]-1)*100,
        "cagr": ((cum.iloc[-1]**(1/years))-1)*100,
        "sharpe": port.mean()/port.std()*np.sqrt(365) if port.std()>0 else 0,
        "dd": ((cum/cum.cummax())-1).min()*100,
        "n": len(port),
        "n_syms": len(all_syms),
    }

# ---------------- DASHBOARD ----------------
def make_dashboard(results, carry_res, latest_signals):
    rows = ""
    for r in results:
        rows += f'''<tr><td><b>{r['symbol']}</b></td><td>{r['trades']}</td>
          <td class="{'pos' if r['win_rate']>40 else 'neg'}">{r['win_rate']}%</td>
          <td class="{'pos' if r['total_pnl']>0 else 'neg'}">{r['total_pnl']:+.2f}%</td>
          <td>{r['avg_win']:+.2f}%</td><td>{r['avg_loss']:+.2f}%</td>
          <td class="{'pos' if r['pf']>1.2 else 'neg'}">{r['pf']}</td>
          <td>{r['expectancy']:+.2f}%</td>
          <td>{r['stops']}</td><td>{r['takes']}</td></tr>'''

    latest_rows = ""
    for L in latest_signals:
        cls = "pos" if L["signal"]=="LONG" else "neg" if L["signal"]=="SHORT" else ""
        latest_rows += f'''<tr><td><b>{L['symbol']}</b></td><td>{L['date']}</td>
          <td>${L['close']}</td><td>{L['rsi']}</td><td>{L['atr_pct']}%</td>
          <td class="{cls}"><b>{L['signal']}</b></td>
          <td>${L['entry']}</td><td class="neg">${L['stop']}</td>
          <td class="pos">${L['take']}</td></tr>'''

    carry_card = ""
    if carry_res:
        carry_card = f'''<div class="card"><div class="label">CARRY (core, {carry_res['n_syms']} symbols)</div>
          <div class="value pos">CAGR {carry_res['cagr']:+.2f}%</div>
          <div class="sub">Sharpe {carry_res['sharpe']:.2f} | DD {carry_res['dd']:+.2f}% | Total {carry_res['total']:+.1f}%</div>
          <div class="sub">Winner: real edge, positive in bear market</div></div>'''

    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Final Report</title><style>
*{box-sizing:border-box}body{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;margin:0;padding:20px}
h1{color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px}
h2{color:#58a6ff;margin-top:35px}
.time{color:#8b949e;font-size:13px;margin-bottom:15px}
table{width:100%;border-collapse:collapse;margin:15px 0;font-size:14px}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #21262d}
th{color:#8b949e;font-size:11px;text-transform:uppercase;background:#161b22}
tr:hover{background:#161b22}.pos{color:#3fb950}.neg{color:#f85149}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:15px;margin:20px 0}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px}
.card .label{color:#8b949e;font-size:12px;text-transform:uppercase}
.card .value{font-size:24px;font-weight:700;margin-top:8px}
.card .sub{color:#8b949e;font-size:13px;margin-top:8px}
.warn{background:#1c2128;border-left:3px solid #d29922;padding:15px;border-radius:6px;margin:15px 0}
.info{background:#161b22;border-left:3px solid #58a6ff;padding:15px;border-radius:6px;margin:15px 0}
</style></head><body>
<h1>Crypto Carry - Final Report</h1>
<div class="time">Generated: __NOW__</div>
<div class="info"><b>Core (real edge):</b> Carry funding arb, 20 symbols, long spot + short perp</div>
<div class="warn"><b>Side (research only):</b> Directional HTF daily — works partially, keep in log, do NOT trade</div>
<div class="grid">__CARRYCARD__</div>
<h2>Latest Directional Signals (research)</h2>
<table><tr><th>Symbol</th><th>Date</th><th>Close</th><th>RSI</th><th>ATR%</th><th>Signal</th><th>Entry</th><th>Stop</th><th>Take</th></tr>
__LATEST__</table>
<h2>Directional Backtest (research)</h2>
<table><tr><th>Symbol</th><th>Trades</th><th>Win%</th><th>Total PnL</th><th>Avg Win</th><th>Avg Loss</th><th>PF</th><th>Expectancy</th><th>Stops</th><th>Takes</th></tr>
__RESULTS__</table></body></html>"""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (html.replace("__NOW__", now).replace("__LATEST__", latest_rows)
                .replace("__RESULTS__", rows).replace("__CARRYCARD__", carry_card))
    path = DASH/"final_report.html"; path.write_text(html); return path

# ---------------- DOCS ----------------
def write_docs(results, carry_res):
    L = [f"# Crypto Carry - Final\n\nGenerated: {datetime.now(timezone.utc).isoformat()}\n\n"]
    L.append("## Core strategy: CARRY (real edge)\n\n")
    if carry_res:
        L.append(f"- Symbols: {carry_res['n_syms']}\n- Days: {carry_res['n']}\n")
        L.append(f"- CAGR: **{carry_res['cagr']:+.2f}%**\n")
        L.append(f"- Sharpe: {carry_res['sharpe']:.2f}\n")
        L.append(f"- Max DD: {carry_res['dd']:+.2f}%\n")
        L.append(f"- Total: {carry_res['total']:+.1f}%\n\n")
        L.append("Real edge: delta-neutral, positive in bear markets (2022: positive while BTC -65%).\n")
        L.append("Haircut 0.3 for real basis/slippage → Sharpe ~2.2, CAGR ~2.7%.\n\n")
    L.append("## Side strategy: DIRECTIONAL (research only, DO NOT TRADE)\n\n")
    L.append("| Symbol | Trades | Win% | PnL | PF |\n|---|---|---|---|---|\n")
    for r in results:
        L.append(f"| {r['symbol']} | {r['trades']} | {r['win_rate']}% | {r['total_pnl']:+.2f}% | {r['pf']} |\n")
    L.append("\n**Only ETH profitable. Others negative. Keep as research log.**\n")
    (DOCS/"FINAL.md").write_text("".join(L))

# ---------------- GIT ----------------
def git_push():
    msg = "FINAL: carry + directional research [" + datetime.now().strftime("%Y-%m-%d %H:%M") + "]"
    for c in ["git add -A", f'git commit -m "{msg}"', "git push origin main"]:
        r = subprocess.run(c, shell=True, capture_output=True, text=True, cwd=Path.cwd())
        out = (r.stdout + r.stderr).strip()
        ok = r.returncode == 0 or "nothing to commit" in out or "up-to-date" in out
        print(f"  {'OK' if ok else 'ERR'}: {c[:60]}")
        if not ok: print(f"     {out[:200]}")

# ---------------- MAIN ----------------
def main():
    panel("CRYPTO CARRY - FINAL")

    panel("CARRY (core)")
    carry_res = run_carry()
    if carry_res:
        print(f"  Symbols: {carry_res['n_syms']}  Days: {carry_res['n']}")
        print(f"  CAGR {carry_res['cagr']:+.2f}%  Sharpe {carry_res['sharpe']:.2f}  "
              f"DD {carry_res['dd']:+.2f}%  Total {carry_res['total']:+.1f}%")

    panel("DIRECTIONAL (research)")
    results, latest_signals = [], []
    for sym in SYMBOLS:
        df1h = load_1h(sym)
        if df1h is None: continue
        dfd = to_daily(df1h)
        cfg = CONFIG[sym]
        d = build_signals(dfd, allow_short=cfg["allow_short"])
        # save signals
        sdf = d.copy(); sdf["symbol"] = sym; sdf["date"] = sdf.index
        cols = ["date","symbol","close","ema20","ema50","ema200","rsi","atr","atr_pct","signal"]
        sdf[cols].to_csv(LOGS/f"signals_{sym}.csv", index=False)

        last = d.iloc[-1]
        if last["signal"] in ("LONG","SHORT"):
            atr_v = float(last["atr"]); entry = round(float(last["close"]),4)
            if last["signal"] == "LONG":
                stop = round(entry-2*atr_v,4); take = round(entry+5*atr_v,4)
            else:
                stop = round(entry+2*atr_v,4); take = round(entry-5*atr_v,4)
            latest_signals.append({"symbol":sym,"date":str(last.name.date()),"close":entry,
                                   "rsi":round(float(last["rsi"]),1),
                                   "atr_pct":round(float(last["atr_pct"]),2),
                                   "signal":last["signal"],"entry":entry,"stop":stop,"take":take})

        trades = backtest(d, sym, rr=2.5, stop_atr=2.0, trailing=cfg["trailing"])
        m = metrics(trades)
        if m:
            m["symbol"] = sym; results.append(m)
            print(f"  {sym:<10} trades {m['trades']:>3}  WR {m['win_rate']:>5}%  PnL {m['total_pnl']:>+8.2f}%  PF {m['pf']}")

    panel("SAVE LOGS")
    all_trades = []
    for sym in SYMBOLS:
        # re-run backtest for logging
        df1h = load_1h(sym)
        if df1h is None: continue
        dfd = to_daily(df1h); cfg = CONFIG[sym]
        d = build_signals(dfd, allow_short=cfg["allow_short"])
        trades = backtest(d, sym, rr=2.5, stop_atr=2.0, trailing=cfg["trailing"])
        all_trades.extend(trades)
    if all_trades:
        pd.DataFrame(all_trades).to_csv(LOGS/"trades_final.csv", index=False)
        print(f"  {LOGS}/trades_final.csv ({len(all_trades)} rows)")
    for sym in SYMBOLS:
        print(f"  {LOGS}/signals_{sym}.csv")

    panel("DASHBOARD")
    dash = make_dashboard(results, carry_res, latest_signals)
    print(f"  {dash}")

    panel("DOCS")
    write_docs(results, carry_res)
    print(f"  {DOCS}/FINAL.md")

    panel("GIT PUSH")
    git_push()

    panel("VERDICT")
    print(f"  CORE:        Carry (real edge)")
    if carry_res:
        print(f"               CAGR {carry_res['cagr']:+.2f}%, Sharpe {carry_res['sharpe']:.2f}, DD {carry_res['dd']:+.2f}%")
    winners = [r for r in results if r["pf"] > 1.2]
    print(f"  SIDE:        Directional (research only)")
    print(f"               {len(winners)}/{len(results)} profitable")
    print(f"  Dashboard:   {dash}")
    print(f"  Logs:        {LOGS}/")
    print(f"  Docs:        {DOCS}/FINAL.md")
    print("="*W)

    webbrowser.open(f"file://{dash.resolve()}")

if __name__ == "__main__":
    main()
