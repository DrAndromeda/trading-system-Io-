"""
TRADING BOT - BTC / ETH / XRP / SOL
====================================
Shows: entry, stop loss, take profit, size, risk
Sources: funding carry + directional trend
Output: HTML dashboard + Telegram signals
"""
import json, os, warnings, webbrowser
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data")
DASH = Path("dashboard"); DASH.mkdir(exist_ok=True)
W = 100
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

PAIRS = {
    "BTCUSDT": {"name": "Bitcoin",  "icon": "B", "color": "#f7931a"},
    "ETHUSDT": {"name": "Ethereum", "icon": "E", "color": "#627eea"},
    "XRPUSDT": {"name": "Ripple",   "icon": "X", "color": "#00aae4"},
    "SOLUSDT": {"name": "Solana",   "icon": "S", "color": "#9945ff"},
}

def panel(t): print("="*W); print("  "+t); print("="*W)

# ---------- DATA ----------
def load_funding(sym, ex="binance"):
    p = DATA/f"raw/{ex}/{sym}_funding.parquet"
    if not p.exists(): return None
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["funding_timestamp"], unit="ms", utc=True).dt.tz_localize(None)
    return df.set_index("ts")["funding_rate"].resample("1D").sum()

def load_prices(sym):
    """npz keys: o,h,l,c,v. No timestamp -> synthetic hourly index."""
    p = DATA/f"cache/{sym}_1h_20000.npz"
    if not p.exists(): return None
    d = np.load(p)
    n = len(d["c"])
    ts = pd.date_range(end=pd.Timestamp.now().floor("h"), periods=n, freq="1h")
    return pd.DataFrame({
        "open": d["o"], "high": d["h"], "low": d["l"],
        "close": d["c"], "volume": d["v"],
    }, index=ts)

# ---------- INDICATORS ----------
def rsi(series, n=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = -delta.clip(upper=0).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# ---------- SIGNALS ----------
def funding_sig(funding, lookback=7, min_ann=5.0):
    if funding is None or len(funding) < 30: return None
    ann = float(funding.rolling(lookback).mean().iloc[-1]) * 3 * 365 * 100
    return {
        "annual_avg": round(ann, 2),
        "active": ann > min_ann,
        "rate_8h": round(float(funding.iloc[-1]) * 100, 4),
    }

def directional_sig(prices):
    if prices is None or len(prices) < 100: return None
    c = prices["close"]
    sma_f = float(c.rolling(20).mean().iloc[-1])
    sma_s = float(c.rolling(50).mean().iloc[-1])
    r = float(rsi(c, 14).iloc[-1])
    a = float(atr(prices, 14).iloc[-1])
    return {
        "price": round(float(c.iloc[-1]), 4),
        "sma_20": round(sma_f, 4),
        "sma_50": round(sma_s, 4),
        "rsi": round(r, 1),
        "atr": round(a, 6),
        "trend": "UP" if sma_f > sma_s else "DOWN",
    }

# ---------- TRADE PLAN ----------
def confidence(d, f):
    s = 50
    if 50 < d["rsi"] < 70: s += 15
    if 30 < d["rsi"] < 50: s += 5
    if d["rsi"] > 75 or d["rsi"] < 25: s -= 15
    if f and f["active"]: s += 20
    if f and f["annual_avg"] > 15: s += 15
    return max(0, min(100, s))

def trade_plan(sym, d, f, capital=10000.0, risk_pct=1.0, rr=2.0):
    price = d["price"]; atr_v = d["atr"]
    rsi_v = d["rsi"]
    if atr_v <= 0: return None

    # RSI-фильтр: не входим в LONG при перекупленности
    if rsi_v > 75:
        action = "WAIT"
        stop_dist = atr_v * 2.0
        take_dist = atr_v * 2.0
        stop = price - stop_dist
        take = price + take_dist
    else:
        direction = "LONG" if d["trend"] == "UP" else "SHORT"
        stop_dist = atr_v * 2.0
        take_dist = stop_dist * rr
        if direction == "LONG":
            stop = price - stop_dist; take = price + take_dist
        else:
            stop = price + stop_dist; take = price - take_dist
        action = direction
        # CARRY только если funding высокий и RSI не экстремальный
        if f and f["active"] and f["annual_avg"] > 8 and rsi_v < 75:
            action = "CARRY"
            stop_dist = atr_v * 3.0
            take_dist = atr_v * 1.0
            stop = price - stop_dist
            take = price + take_dist

    risk_usd = capital * risk_pct / 100
    size = risk_usd / stop_dist if stop_dist > 0 else 0

    return {
        "symbol": sym,
        "name": PAIRS[sym]["name"],
        "icon": PAIRS[sym]["icon"],
        "color": PAIRS[sym]["color"],
        "action": action,
        "entry": round(price, 4),
        "stop": round(stop, 4),
        "take": round(take, 4),
        "stop_pct": round(stop_dist / price * 100, 2),
        "take_pct": round(take_dist / price * 100, 2),
        "size": round(size, 6),
        "risk_usd": round(risk_usd, 2),
        "rsi": d["rsi"],
        "atr": d["atr"],
        "funding_ann": f["annual_avg"] if f else 0,
        "confidence": confidence(d, f),
    }

# ---------- TELEGRAM ----------
def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        print("  [TG DRY-RUN]")
        print("  " + text.replace("\n", "\n  ")[:400])
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

def format_tg(plans):
    L = ["<b>Trade Signals</b>",
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), ""]
    for p in plans:
        emoji = "[LONG]" if p["action"] == "LONG" else "[SHORT]" if p["action"] == "SHORT" else "[CARRY]"
        L.append(f"{emoji} <b>{p['name']}</b> ({p['symbol']})")
        L.append(f"  Entry: <code>{p['entry']}</code>")
        L.append(f"  Stop:  <code>{p['stop']}</code> ({p['stop_pct']:+.2f}%)")
        L.append(f"  Take:  <code>{p['take']}</code> ({p['take_pct']:+.2f}%)")
        L.append(f"  Size:  {p['size']:.6f} | Risk: ${p['risk_usd']}")
        L.append(f"  RSI: {p['rsi']:.0f} | Conf: {p['confidence']}% | Funding: {p['funding_ann']:+.1f}%")
        L.append("")
    return "\n".join(L)

# ---------- DASHBOARD ----------
def make_dashboard(plans, prices_data):
    cards = ""
    for p in plans:
        cls = "long" if p["action"] == "LONG" else "short" if p["action"] == "SHORT" else "carry"
        cards += f'''
        <div class="trade-card {cls}">
          <div class="tc-header">
            <span class="tc-icon" style="color:{p['color']}">{p['icon']}</span>
            <span class="tc-name">{p['name']}</span>
            <span class="tc-action {cls}">{p['action']}</span>
          </div>
          <div class="tc-price">${p['entry']:,.4f}</div>
          <div class="tc-rows">
            <div class="tc-row"><span class="lbl">Entry</span><span class="val">${p['entry']}</span></div>
            <div class="tc-row"><span class="lbl">Stop Loss</span><span class="val neg">${p['stop']} <small>({p['stop_pct']:+.2f}%)</small></span></div>
            <div class="tc-row"><span class="lbl">Take Profit</span><span class="val pos">${p['take']} <small>({p['take_pct']:+.2f}%)</small></span></div>
            <div class="tc-row"><span class="lbl">Size</span><span class="val">{p['size']:.6f}</span></div>
            <div class="tc-row"><span class="lbl">Risk</span><span class="val">${p['risk_usd']}</span></div>
          </div>
          <div class="tc-footer">
            <div class="metric"><span>RSI</span><b>{p['rsi']:.0f}</b></div>
            <div class="metric"><span>Funding</span><b>{p['funding_ann']:+.1f}%</b></div>
            <div class="metric"><span>Conf</span><b>{p['confidence']}%</b></div>
          </div>
          <div class="conf-bar"><div class="conf-fill" style="width:{p['confidence']}%"></div></div>
        </div>'''

    charts = []
    for sym, pdf in prices_data.items():
        if pdf is None or len(pdf) < 50: continue
        last = pdf.tail(500)
        charts.append({
            "symbol": sym,
            "name": PAIRS.get(sym, {}).get("name", sym),
            "color": PAIRS.get(sym, {}).get("color", "#58a6ff"),
            "dates": [d.strftime("%m-%d %H:%M") for d in last.index],
            "prices": [round(float(x), 4) for x in last["close"].values],
        })

    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Trading Bot - Live Orders</title>
<style>
*{box-sizing:border-box}
body{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;margin:0;padding:20px}
h1{color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px;margin-top:0}
h2{color:#58a6ff;margin-top:40px}
.header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap}
.time{color:#8b949e;font-size:13px}
.trades{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px;margin:20px 0}
.trade-card{background:linear-gradient(135deg,#161b22 0%,#1c2128 100%);border:1px solid #30363d;border-radius:12px;padding:20px}
.trade-card.long{border-left:4px solid #3fb950}
.trade-card.short{border-left:4px solid #f85149}
.trade-card.carry{border-left:4px solid #d29922}
.tc-header{display:flex;align-items:center;gap:10px;margin-bottom:15px}
.tc-icon{font-size:28px;font-weight:bold;width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:#21262d;border-radius:8px}
.tc-name{font-size:18px;font-weight:600;flex:1}
.tc-action{font-size:12px;font-weight:700;padding:4px 10px;border-radius:6px}
.tc-action.long{background:rgba(63,185,80,0.2);color:#3fb950}
.tc-action.short{background:rgba(248,81,73,0.2);color:#f85149}
.tc-action.carry{background:rgba(210,153,34,0.2);color:#d29922}
.tc-price{font-size:24px;font-weight:700;color:#fff;margin-bottom:15px;font-family:monospace}
.tc-rows{display:flex;flex-direction:column;gap:8px;margin-bottom:15px}
.tc-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #21262d}
.tc-row .lbl{color:#8b949e;font-size:13px}
.tc-row .val{font-weight:600;font-family:monospace;font-size:14px}
.tc-row .val small{color:#8b949e;font-weight:400;font-size:11px}
.pos{color:#3fb950}.neg{color:#f85149}
.tc-footer{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px;padding-top:12px;border-top:1px solid #21262d}
.metric{text-align:center}
.metric span{display:block;color:#8b949e;font-size:11px;text-transform:uppercase}
.metric b{display:block;color:#c9d1d9;font-size:16px;margin-top:4px}
.conf-bar{height:4px;background:#21262d;border-radius:2px;margin-top:12px;overflow:hidden}
.conf-fill{height:100%;background:linear-gradient(90deg,#3fb950,#58a6ff)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px;margin-top:20px}
.chart-card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:15px}
.chart-title{font-size:14px;font-weight:600;margin-bottom:10px;color:#8b949e}
canvas{max-height:200px}
</style></head>
<body>
<div class="header">
  <h1>Trading Bot - Live Orders</h1>
  <div class="time">Generated: <span id="ts"></span></div>
</div>
<h2>Trade Plans</h2>
<div class="trades">__CARDS__</div>
<h2>Price Charts (last 500h)</h2>
<div class="charts" id="charts"></div>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
document.getElementById('ts').textContent = "__NOW__";
const data = __CHART_DATA__;
for (const c of data) {
  const id = 'chart_' + c.symbol;
  const div = document.createElement('div');
  div.className = 'chart-card';
  div.innerHTML = '<div class="chart-title">' + c.name + ' (' + c.symbol + ')</div><canvas id="' + id + '"></canvas>';
  document.getElementById('charts').appendChild(div);
  new Chart(document.getElementById(id), {
    type:'line',
    data:{labels:c.dates,datasets:[{label:c.symbol,data:c.prices,borderColor:c.color,backgroundColor:c.color+'22',fill:true,tension:0.2,pointRadius:0,borderWidth:2}]},
    options:{responsive:true,plugins:{legend:{display:false}},scales:{x:{ticks:{maxTicksLimit:6,color:'#8b949e',font:{size:10}},grid:{color:'#21262d'}},y:{ticks:{color:'#8b949e',font:{size:10}},grid:{color:'#21262d'}}}}
  });
}
</script>
</body></html>"""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = html.replace("__CARDS__", cards).replace("__NOW__", now).replace("__CHART_DATA__", json.dumps(charts))
    path = DASH/"trading_bot.html"
    path.write_text(html)
    return path

# ---------- MAIN ----------
def main():
    panel("TRADING BOT - BTC / ETH / XRP / SOL")

    panel("STEP 1: SCAN")
    plans = []; prices_data = {}
    for sym in PAIRS:
        print(f"\n  [{PAIRS[sym]['icon']}] {PAIRS[sym]['name']} ({sym})")
        f = load_funding(sym)
        fs = funding_sig(f) if f is not None else None
        if fs: print(f"    funding: {fs['annual_avg']:+.2f}% ann  active={fs['active']}")
        else: print(f"    funding: no data")

        prices = load_prices(sym)
        ds = directional_sig(prices) if prices is not None else None
        if ds:
            prices_data[sym] = prices
            print(f"    price:   ${ds['price']}  trend={ds['trend']}  RSI={ds['rsi']}")
            plan = trade_plan(sym, ds, fs)
            if plan: plans.append(plan)
        else:
            print(f"    price:   no cache")

    if not plans:
        print("\n  No signals")
        return

    panel("STEP 2: TRADE PLANS")
    print(f"  {'pair':<10}{'action':>7}{'entry':>13}{'stop':>13}{'take':>13}{'risk$':>8}{'conf':>6}")
    print("  " + "-"*(W-2))
    for p in plans:
        print(f"  {p['name']:<10}{p['action']:>7}{p['entry']:>13.4f}{p['stop']:>13.4f}"
              f"{p['take']:>13.4f}{p['risk_usd']:>8.2f}{p['confidence']:>5}%")

    panel("STEP 3: DASHBOARD")
    path = make_dashboard(plans, prices_data)
    print(f"  Written: {path}")

    panel("STEP 4: TELEGRAM")
    msg = format_tg(plans)
    sent = tg_send(msg)
    print(f"  Sent: {sent}")
    if not TG_TOKEN:
        print("  To enable:")
        print("    1. @BotFather -> /newbot -> get TOKEN")
        print("    2. https://api.telegram.org/bot<TOKEN>/getUpdates -> chat.id")
        print("    3. TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... python3 trading_bot.py")

    panel("STEP 5: OPEN DASHBOARD")
    webbrowser.open(f"file://{path.resolve()}")

    panel("VERDICT")
    print(f"  Scanned: {len(plans)} pairs")
    print(f"  Dashboard: {path}")
    print(f"  Telegram: {'sent' if sent else 'dry-run'}")
    print("="*W)

if __name__ == "__main__":
    main()
