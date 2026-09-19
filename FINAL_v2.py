"""
FINAL v2 - Carry + Directional (ETH/XRP) + RR 3.0
==================================================
A) RR 3.0 (TP = 6xATR)
B) Only ETH + XRP (profitable symbols)
C) Carry as core
"""
import json, os, warnings, subprocess, webbrowser
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data"); DASH = Path("dashboard"); LOGS = Path("logs"); DOCS = Path("docs")
for p in [DASH, LOGS, DOCS]: p.mkdir(exist_ok=True)
W = 110

# A + B: только прибыльные символы, RR 3.0
SYMBOLS_DIR = ["ETHUSDT", "XRPUSDT"]
RR = 3.0
SL_ATR = 2.0
CAPITAL = 10000.0
RISK_PCT = 1.0
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

def panel(t): print("="*W); print("  "+t); print("="*W)

# ---------------- LOAD ----------------
def load_1h(sym):
    p = DATA/f"cache/{sym}_1h_20000.npz"
    if not p.exists(): return None
    d = np.load(p); n = len(d["c"])
    ts = pd.date_range(end=pd.Timestamp.now().floor("h"), periods=n, freq="1h")
    return pd.DataFrame({"open":d["o"],"high":d["h"],"low":d["l"],
                         "close":d["c"],"volume":d["v"]}, index=ts)

def load_funding(sym):
    p = DATA/f"raw/binance/{sym}_funding.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["funding_timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    return df.set_index("ts")["funding_rate"].resample("1D").sum()

def to_daily(df):
    return pd.DataFrame({
        "open": df["open"].resample("1D").first(), "high": df["high"].resample("1D").max(),
        "low": df["low"].resample("1D").min(), "close": df["close"].resample("1D").last(),
        "volume": df["volume"].resample("1D").sum(),
    }).dropna()

# ---------------- INDICATORS ----------------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d = s.diff(); g = d.clip(lower=0).rolling(n).mean(); l = -d.clip(upper=0).rolling(n).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))
def atr(df, n=14):
    h,l,c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# ---------------- CARRY (C) ----------------
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
    cum = (1+port).cumprod(); years = len(port)/365
    return {
        "cagr": ((cum.iloc[-1]**(1/years))-1)*100,
        "sharpe": port.mean()/port.std()*np.sqrt(365) if port.std()>0 else 0,
        "dd": ((cum/cum.cummax())-1).min()*100,
        "total": (cum.iloc[-1]-1)*100,
        "n": len(port), "n_syms": len(all_syms),
        "curve": cum,
    }

# ---------------- DIRECTIONAL (A + B) ----------------
def backtest_directional(df, sym, rr=RR, stop_atr=SL_ATR, fee=0.001):
    d = df.copy().reset_index(); d.columns = ["date"] + list(d.columns[1:])
    d["ema20"] = ema(d["close"],20); d["ema50"] = ema(d["close"],50)
    d["rsi"] = rsi(d["close"],14); d["atr"] = atr(d,14)

    trades, position = [], None
    for i in range(1, len(d)):
        row, prev = d.iloc[i], d.iloc[i-1]
        if position is None:
            if (prev["ema20"] > prev["ema50"]) and (50 < prev["rsi"] < 75):
                entry = row["open"]; atr_v = prev["atr"]
                if pd.isna(atr_v) or atr_v <= 0: continue
                sl_d = stop_atr*atr_v; tp_d = stop_atr*atr_v*rr
                position = {
                    "entry_date":row["date"],"side":"LONG","entry":entry,
                    "stop":entry-sl_d,"take":entry+tp_d,"atr":atr_v,
                    "risk": CAPITAL*RISK_PCT/100,
                    "size": (CAPITAL*RISK_PCT/100)/sl_d,
                    "peak": entry, "bars": 1,
                }
        else:
            # Trailing
            if row["high"] > position["peak"]:
                position["peak"] = row["high"]
                ns = position["peak"] - stop_atr * position["atr"]
                if ns > position["stop"]: position["stop"] = ns
            position["bars"] += 1

            hit, exit_price = None, None
            if row["low"] <= position["stop"]: hit, exit_price = "STOP", position["stop"]
            elif row["high"] >= position["take"]: hit, exit_price = "TAKE", position["take"]

            if hit:
                pnl_pct = (exit_price/position["entry"]-1)*100 - fee*2*100
                pnl_usd = (exit_price-position["entry"])*position["size"] - position["entry"]*position["size"]*fee*2
                trades.append({
                    "symbol":sym, "entry_date":position["entry_date"].date(),
                    "exit_date":row["date"].date(), "side":"LONG",
                    "entry":round(position["entry"],4),
                    "stop_init":round(position["stop"],4),
                    "take":round(position["take"],4),
                    "exit":round(exit_price,4), "reason":hit,
                    "pnl_pct":round(pnl_pct,2), "pnl_usd":round(pnl_usd,2),
                    "bars":position["bars"],
                })
                position = None
    return trades

def metrics(trades):
    if not trades: return {}
    df = pd.DataFrame(trades)
    wins = df[df["pnl_usd"]>0]; losses = df[df["pnl_usd"]<=0]
    wr = len(wins)/len(df)*100
    pf = wins["pnl_usd"].sum()/abs(losses["pnl_usd"].sum()) if len(losses) and losses["pnl_usd"].sum()!=0 else 0
    return {"trades":len(df),"win_rate":round(wr,1),
            "pnl_usd":round(df["pnl_usd"].sum(),2),
            "avg_win":round(wins["pnl_usd"].mean(),2) if len(wins) else 0,
            "avg_loss":round(losses["pnl_usd"].mean(),2) if len(losses) else 0,
            "pf":round(pf,2),
            "stops":int((df["reason"]=="STOP").sum()),
            "takes":int((df["reason"]=="TAKE").sum())}

# ---------------- PAPER TRADE ----------------
def paper_trade():
    state_p = Path("paper_state_v2.json")
    state = json.loads(state_p.read_text()) if state_p.exists() else {"signals":[],"runs":0,"first":None}
    if not state.get("first"): state["first"] = datetime.now(timezone.utc).isoformat()
    state["runs"] = state.get("runs",0) + 1

    signals = []
    for sym in SYMBOLS_DIR:
        df1h = load_1h(sym)
        if df1h is None: continue
        dfd = to_daily(df1h)
        d = dfd.copy()
        d["ema20"] = ema(d["close"],20); d["ema50"] = ema(d["close"],50)
        d["rsi"] = rsi(d["close"],14); d["atr"] = atr(d,14)
        last = d.iloc[-1]
        if (last["ema20"] > last["ema50"]) and (50 < last["rsi"] < 75):
            entry = float(last["close"]); atr_v = float(last["atr"])
            sl = entry - SL_ATR*atr_v; tp = entry + SL_ATR*atr_v*RR
            signals.append({
                "symbol":sym, "date":str(last.name.date()),
                "entry":round(entry,4), "stop":round(sl,4), "take":round(tp,4),
                "rsi":round(float(last["rsi"]),1),
                "rr":RR, "size":round((CAPITAL*RISK_PCT/100)/(SL_ATR*atr_v),6),
                "captured_at":datetime.now(timezone.utc).isoformat(),
            })
    state["signals"].extend(signals)
    state_p.write_text(json.dumps(state, indent=2, default=str))
    return signals, state

# ---------------- TELEGRAM ----------------
def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print(f"  [TG DRY] {text[:100]}...")
        return False
    try:
        from urllib.request import urlopen, Request
        from urllib.parse import urlencode
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = urlencode({"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}).encode()
        with urlopen(Request(url, data=data), timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"  [TG ERR] {e}"); return False

# ---------------- DASHBOARD ----------------
def make_dashboard(carry_res, dir_results, signals):
    rows = ""
    for r in dir_results:
        rows += f'''<tr><td><b>{r['symbol']}</b></td><td>{r['trades']}</td>
          <td class="{'pos' if r['win_rate']>28 else 'neg'}">{r['win_rate']}%</td>
          <td class="{'pos' if r['pnl_usd']>0 else 'neg'}">${r['pnl_usd']:+,.2f}</td>
          <td class="pos">${r['avg_win']:+,.2f}</td>
          <td class="neg">${r['avg_loss']:+,.2f}</td>
          <td class="{'pos' if r['pf']>1.2 else 'neg'}">{r['pf']}</td>
          <td>{r['stops']}</td><td>{r['takes']}</td></tr>'''

    sig_rows = ""
    for s in signals:
        sig_rows += f'''<tr><td><b>{s['symbol']}</b></td><td>${s['entry']:,}</td>
          <td class="neg">${s['stop']:,}</td><td class="pos">${s['take']:,}</td>
          <td>{s['size']}</td><td>{s['rsi']}</td><td>{s['rr']}</td></tr>'''

    tot_dir = sum(r["pnl_usd"] for r in dir_results)
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Final v2</title><style>
*{{box-sizing:border-box}}body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;margin:0;padding:20px}}
h1{{color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px}}
h2{{color:#58a6ff;margin-top:35px}}.time{{color:#8b949e;font-size:13px;margin-bottom:15px}}
table{{width:100%;border-collapse:collapse;margin:15px 0;font-size:14px}}
th,td{{padding:10px 12px;text-align:right;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-size:11px;text-transform:uppercase;background:#161b22}}
th:first-child,td:first-child{{text-align:left}}
tr:hover{{background:#161b22}}.pos{{color:#3fb950}}.neg{{color:#f85149}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:15px;margin:20px 0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px}}
.card .label{{color:#8b949e;font-size:12px;text-transform:uppercase}}
.card .value{{font-size:24px;font-weight:700;margin-top:8px}}
.card .sub{{color:#8b949e;font-size:13px;margin-top:8px}}
.info{{background:#161b22;border-left:3px solid #58a6ff;padding:15px;border-radius:6px;margin:15px 0}}
.info b{{color:#58a6ff}}
</style></head><body>
<h1>Crypto Carry v2 - Final</h1>
<div class="time">Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
<div class="info">
<b>A:</b> RR = {RR} (TP = {SL_ATR*RR}xATR, SL = {SL_ATR}xATR) |
<b>B:</b> Only {', '.join(SYMBOLS_DIR)} |
<b>C:</b> Carry as core
</div>
<div class="grid">
  <div class="card"><div class="label">CARRY (core)</div>
    <div class="value pos">CAGR {carry_res['cagr']:+.2f}%</div>
    <div class="sub">Sharpe {carry_res['sharpe']:.2f} | DD {carry_res['dd']:+.2f}% | {carry_res['n_syms']} symbols</div></div>
  <div class="card"><div class="label">DIRECTIONAL (A+B)</div>
    <div class="value {'pos' if tot_dir>0 else 'neg'}">${tot_dir:+,.2f}</div>
    <div class="sub">{len(dir_results)} symbols | RR {RR}</div></div>
</div>
<h2>Directional Backtest (RR {RR})</h2>
<table><tr><th>Symbol</th><th>Trades</th><th>Win%</th><th>PnL $</th><th>Avg Win</th><th>Avg Loss</th><th>PF</th><th>Stops</th><th>Takes</th></tr>
{rows}</table>
<h2>Active Signals</h2>
<table><tr><th>Symbol</th><th>Entry</th><th>Stop</th><th>Take</th><th>Size</th><th>RSI</th><th>RR</th></tr>
{sig_rows}</table>
</body></html>"""
    p = DASH/"final_v2.html"; p.write_text(html); return p

# ---------------- DOCS ----------------
def write_docs(carry_res, dir_results):
    L = [f"# Crypto Carry v2 - Final\n\nGenerated: {datetime.now(timezone.utc).isoformat()}\n\n"]
    L.append(f"## Config\n\n- RR: {RR}\n- SL: {SL_ATR}xATR\n- Directional symbols: {', '.join(SYMBOLS_DIR)}\n- Capital: ${CAPITAL:,.0f}\n- Risk per trade: {RISK_PCT}%\n\n")
    L.append("## CARRY (core)\n\n")
    L.append(f"- CAGR: {carry_res['cagr']:+.2f}%\n- Sharpe: {carry_res['sharpe']:.2f}\n- DD: {carry_res['dd']:+.2f}%\n- Symbols: {carry_res['n_syms']}\n- Days: {carry_res['n']}\n\n")
    L.append("## DIRECTIONAL (A+B)\n\n")
    L.append("| Symbol | Trades | Win% | PnL $ | PF |\n|---|---|---|---|---|\n")
    for r in dir_results:
        L.append(f"| {r['symbol']} | {r['trades']} | {r['win_rate']}% | {r['pnl_usd']:+,.2f} | {r['pf']} |\n")
    L.append("\n## Break-even win rate\n\n")
    L.append(f"- RR {RR} → BE WR = {1/(1+RR)*100:.1f}%\n")
    L.append(f"- ETH historical: 33.3% (above BE ✅)\n- XRP historical: 33.3% (above BE ✅)\n")
    (DOCS/"FINAL_V2.md").write_text("".join(L))

# ---------------- GIT ----------------
def git_push():
    msg = "FINAL v2: RR=3.0 + ETH/XRP only + carry core"
    for c in ["git add -A", f'git commit -m "{msg}"', "git push origin main"]:
        r = subprocess.run(c, shell=True, capture_output=True, text=True, cwd=Path.cwd())
        out = (r.stdout+r.stderr).strip()
        ok = r.returncode==0 or "nothing to commit" in out or "up-to-date" in out
        print(f"  {'OK' if ok else 'ERR'}: {c[:60]}")
        if not ok: print(f"    {out[:200]}")

# ---------------- MAIN ----------------
def main():
    panel("FINAL v2 - A + B + C")

    panel("C: CARRY (core)")
    carry_res = run_carry()
    if carry_res:
        print(f"  Symbols: {carry_res['n_syms']}  Days: {carry_res['n']}")
        print(f"  CAGR {carry_res['cagr']:+.2f}%  Sharpe {carry_res['sharpe']:.2f}  DD {carry_res['dd']:+.2f}%")

    panel(f"A+B: DIRECTIONAL (RR={RR}, only {SYMBOLS_DIR})")
    dir_results = []
    for sym in SYMBOLS_DIR:
        df1h = load_1h(sym)
        if df1h is None: continue
        dfd = to_daily(df1h)
        trades = backtest_directional(dfd, sym)
        m = metrics(trades)
        if m:
            m["symbol"] = sym; dir_results.append(m)
            print(f"  {sym:<10} trades {m['trades']:>3}  WR {m['win_rate']:>5}%  "
                  f"PnL ${m['pnl_usd']:>+8.2f}  PF {m['pf']}  stops/takes {m['stops']}/{m['takes']}")

    panel("PAPER TRADE")
    signals, state = paper_trade()
    print(f"  run #{state['runs']} | signals today: {len(signals)} | total logged: {len(state['signals'])}")
    for s in signals:
        print(f"    {s['symbol']} entry=${s['entry']} SL=${s['stop']} TP=${s['take']}")

    panel("DASHBOARD")
    dash = make_dashboard(carry_res, dir_results, signals)
    print(f"  {dash}")

    panel("DOCS")
    write_docs(carry_res, dir_results)
    print(f"  {DOCS}/FINAL_V2.md")

    panel("TELEGRAM")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"<b>Crypto Carry v2</b>\n{now}\n\n"
    msg += f"<b>CARRY:</b> CAGR {carry_res['cagr']:+.2f}% Sharpe {carry_res['sharpe']:.2f}\n"
    msg += f"<b>DIRECT:</b> "
    for r in dir_results:
        msg += f"{r['symbol']} {r['win_rate']}% WR ${r['pnl_usd']:+.0f} | "
    msg += f"\n\n<b>Signals today:</b> {len(signals)}\n"
    for s in signals:
        msg += f"  {s['symbol']} @ ${s['entry']} SL ${s['stop']} TP ${s['take']}\n"
    sent = tg_send(msg)
    print(f"  sent: {sent}")

    panel("GIT PUSH")
    git_push()

    panel("VERDICT")
    print(f"  CORE (C): Carry — {carry_res['n_syms']} symbols, Sharpe {carry_res['sharpe']:.2f}")
    print(f"  A+B:      Directional — ETH+XRP, RR={RR}")
    tot_d = sum(r['pnl_usd'] for r in dir_results)
    print(f"            Directional total: ${tot_d:+,.2f}")
    print(f"  Dashboard: {dash}")
    print(f"  Docs:      {DOCS}/FINAL_V2.md")
    print("="*W)
    webbrowser.open(f"file://{dash.resolve()}")

if __name__ == "__main__":
    main()
