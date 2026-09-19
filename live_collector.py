"""
LIVE COLLECTOR v7 — 9 timeframes (15m/1h/4h/6h/12h/1d/3d/1w/1M)
"""
import json, os, warnings, time, subprocess
import numpy as np, pandas as pd
import ccxt
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
ROOT = Path("/Users/andromeda/crypto-carry")
DATA = ROOT/"data"; DASH = ROOT/"dashboard"; LOGS = ROOT/"logs"
for p in [DASH, LOGS]: p.mkdir(exist_ok=True)
STATE = ROOT/"live_state.json"
SIGNALS_LOG = LOGS/"signals_confirmed.jsonl"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "LINKUSDT", "SOLUSDT", "PAXGUSDT"]
TIMEFRAMES = ["15m", "1h", "4h", "6h", "12h", "1d", "3d", "1w", "1M"]
RR = 2.5
SL_ATR = 2.0
CAPITAL = 10000.0
RISK_PCT = 1.0
MAX_POSITIONS = 5
MAX_NOTIONAL_PER_TRADE = 15000
POLL_SEC = 180
PUSH_EVERY_N = 12
MIN_CONFIDENCE = 75

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def tg_send(text):
    if not TG_TOKEN or not TG_CHAT: return False
    try:
        from urllib.request import urlopen, Request
        from urllib.parse import urlencode
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = urlencode({"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}).encode()
        with urlopen(Request(url, data=data), timeout=10) as r:
            return r.status == 200
    except Exception as e:
        log(f"TG err: {e}"); return False

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

def load_state():
    if STATE.exists():
        s = json.loads(STATE.read_text())
        for k, v in [("signals",[]),("pushes",0),("cycle",0),("positions",{}),("closed",[])]:
            if k not in s: s[k] = v
        return s
    return {"signals": [], "pushes": 0, "cycle": 0, "positions": {}, "closed": [],
            "started": datetime.now(timezone.utc).isoformat()}

def save_state(s):
    STATE.write_text(json.dumps(s, indent=2, default=str))

def git_push():
    try:
        for c in [["git","add","-A"],
                  ["git","commit","-m",f"auto: {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
                  ["git","push","origin","main"]]:
            r = subprocess.run(c, cwd=ROOT, capture_output=True, text=True, timeout=30)
            out = (r.stdout + r.stderr).strip()
            if r.returncode != 0 and "nothing to commit" not in out and "up-to-date" not in out:
                log(f"git: {out[:100]}"); return False
        log("✅ pushed"); return True
    except Exception as e:
        log(f"git err: {e}"); return False

def fetch_ohlcv(ex, sym, tf, limit=200):
    for attempt in range(3):
        try:
            data = ex.fetch_ohlcv(sym, tf, limit=limit)
            df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_localize(None)
            return df.set_index("ts")
        except Exception as e:
            if attempt < 2: time.sleep(2 * (attempt + 1))
            else: raise

def fetch_funding(ex, sym):
    try:
        fr = ex.fetch_funding_rate(sym)
        return fr.get("fundingRate", 0) or 0
    except Exception:
        # Монета без фьючерсов (золото и др.) — funding = 0
        return 0

def analyze_tf(df, tf_name):
    if len(df) < 50:
        log(f"    analyze_tf {tf_name}: only {len(df)} bars (need 50)")
        return None
    d = df.copy()
    d["ema20"] = ema(d["close"],20)
    d["ema50"] = ema(d["close"],50)
    d["sma100"] = d["close"].rolling(100).mean()
    d["rsi"] = rsi(d["close"],14)
    d["atr"] = atr(d,14)
    d["atr_pct"] = d["atr"]/d["close"]*100
    d["vol_sma20"] = d["volume"].rolling(20).mean()
    last = d.iloc[-1]
    prev_rsi = d["rsi"].iloc[-6] if len(d) > 6 else last["rsi"]
    trend_up = last["ema20"] > last["ema50"]
    above_sma = last["close"] > last["sma100"]
    ranges = {
        "15m": (45, 72, 78), "1h": (50, 68, 75),
        "4h": (50, 85, 90), "6h": (50, 85, 90),
        "12h": (45, 85, 90), "1d": (40, 82, 88),
        "3d": (35, 82, 88), "1w": (30, 82, 88), "1M": (25, 82, 88),
    }
    rsi_low, rsi_high, ob = ranges.get(tf_name, (40, 85, 88))
    rsi_ok = rsi_low < last["rsi"] < rsi_high
    rsi_rising = last["rsi"] > prev_rsi
    atr_ok = last["atr_pct"] < 3.0
    volume_ok = last["volume"] > last["vol_sma20"]
    bullish = trend_up and above_sma and rsi_ok
    if bullish and rsi_rising and volume_ok: sig = "STRONG"
    elif bullish: sig = "WEAK"
    elif last["rsi"] > ob: sig = "OVERBOUGHT"
    elif last["rsi"] < 30: sig = "OVERSOLD"
    else: sig = "WAIT"
    return {"signal": sig, "price": float(last["close"]),
            "rsi": float(last["rsi"]), "atr": float(last["atr"]),
            "atr_pct": float(last["atr_pct"]),
            "trend_up": bool(trend_up), "above_sma": bool(above_sma),
            "rsi_ok": bool(rsi_ok), "volume_ok": bool(volume_ok)}

def make_chart(df_tf, bars=80):
    c = df_tf.tail(bars).copy()
    c_ema20 = c["close"].ewm(span=20, adjust=False).mean()
    c_ema50 = c["close"].ewm(span=50, adjust=False).mean()
    return {
        "times": [t.strftime("%m-%d %H:%M") for t in c.index],
        "close": [round(float(x),6) for x in c["close"].values],
        "ema20": [round(float(x),6) for x in c_ema20.values],
        "ema50": [round(float(x),6) for x in c_ema50.values],
    }

def compute_mtf_signal(ex, sym):
    dfs = {}
    for tf in TIMEFRAMES:
        try:
            dfs[tf] = fetch_ohlcv(ex, sym, tf, 200); time.sleep(0.25)
        except Exception as e:
            log(f"  {sym} {tf} err: {e}"); return None
    analyses = {tf: analyze_tf(dfs[tf], tf) for tf in TIMEFRAMES}
    if any(a is None for a in analyses.values()): return None
    a15, a1h, a4h, a6h, a12h, a1d, a3d, a1w, a1M = [analyses[tf] for tf in TIMEFRAMES]

    conf = 0
    if a15["trend_up"]: conf += 8
    if a15["rsi_ok"]: conf += 7
    if a15["volume_ok"]: conf += 5
    if a1h["trend_up"]: conf += 8
    if a1h["above_sma"]: conf += 4
    if a1h["rsi_ok"]: conf += 8
    if a1h["volume_ok"]: conf += 4
    if a1h["atr_pct"] < 3: conf += 4
    if a4h["trend_up"]: conf += 8
    if a4h["signal"] in ("STRONG","WEAK"): conf += 6
    if a6h["trend_up"]: conf += 5
    if a6h["signal"] in ("STRONG","WEAK"): conf += 4
    if a12h["trend_up"]: conf += 5
    if a12h["signal"] in ("STRONG","WEAK"): conf += 4
    if a1d["trend_up"]: conf += 4
    if a1d["signal"] in ("STRONG","WEAK"): conf += 4
    if a3d["trend_up"]: conf += 4
    if a1w["trend_up"]: conf += 3
    if a1M["trend_up"]: conf += 3

    funding = fetch_funding(ex, sym)
    if funding > 0: conf += 3
    if funding * 3 * 365 * 100 > 10: conf += 3

    critical = [a15, a1h, a4h, a1d]
    all_bullish = all(a["signal"] in ("STRONG","WEAK") for a in critical)

    if all_bullish and conf >= MIN_CONFIDENCE: signal = "LONG"
    elif all_bullish and conf >= 60: signal = "WEAK_LONG"
    elif a15["signal"] == "OVERBOUGHT": signal = "OVERBOUGHT"
    elif a15["signal"] == "OVERSOLD": signal = "OVERSOLD"
    else: signal = "WAIT"

    entry = a15["price"]; atr_v = a15["atr"]
    if atr_v > 0:
        stop = entry - SL_ATR*atr_v
        take = entry + SL_ATR*atr_v*RR
        size_by_risk = (CAPITAL*RISK_PCT/100) / (SL_ATR*atr_v)
        size_by_notional = MAX_NOTIONAL_PER_TRADE / entry
        size = min(size_by_risk, size_by_notional)
    else:
        stop = take = entry; size = 0

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": sym, "signal": signal, "confidence": min(conf, 100),
        "price": round(entry,6),
        "rsi": {tf: round(analyses[tf]["rsi"],1) for tf in TIMEFRAMES},
        "trend": {tf: ("up" if analyses[tf]["trend_up"] else "down") for tf in TIMEFRAMES},
        "atr_pct": round(a15["atr_pct"],2),
        "entry": round(entry,6), "stop": round(stop,6), "take": round(take,6),
        "size": round(size,6), "rr": RR,
        "funding_ann": round(funding * 3 * 365 * 100, 2),
        "charts": {tf: {**make_chart(dfs[tf], 80),
                        "entry": round(entry, 6),
                        "stop": round(stop, 6),
                        "take": round(take, 6)} for tf in TIMEFRAMES},
    }

def update_positions(state, latest):
    positions = state.get("positions", {})
    closed = state.get("closed", [])
    now_iso = datetime.now(timezone.utc).isoformat()
    latest_map = {s["symbol"]: s for s in latest}
    for sym in list(positions.keys()):
        if sym not in latest_map: continue
        pos = positions[sym]
        price = latest_map[sym]["price"]
        initial_risk = pos["entry"] - pos["initial_stop"]
        if (price - pos["entry"]) > initial_risk:
            new_stop = price - initial_risk
            if new_stop > pos["stop"]:
                pos["stop"] = round(new_stop,6)
                pos["trail_updates"] = pos.get("trail_updates",0) + 1
        if price <= pos["stop"]:
            pnl_pct = (pos["stop"]/pos["entry"] - 1)*100
            closed.append({**pos, "exit_ts": now_iso, "exit_price": pos["stop"],
                          "exit_reason": "STOP", "pnl_pct": round(pnl_pct,2)})
            del positions[sym]; log(f"  🔴 {sym} STOP @ ${pos['stop']} ({pnl_pct:+.2f}%)")
        elif price >= pos["take"]:
            pnl_pct = (pos["take"]/pos["entry"] - 1)*100
            closed.append({**pos, "exit_ts": now_iso, "exit_price": pos["take"],
                          "exit_reason": "TAKE", "pnl_pct": round(pnl_pct,2)})
            del positions[sym]; log(f"  🟢 {sym} TAKE @ ${pos['take']} ({pnl_pct:+.2f}%)")
    state["positions"] = positions
    state["closed"] = closed[-100:]

def open_new_positions(state, confirmed):
    positions = state.get("positions", {})
    now_iso = datetime.now(timezone.utc).isoformat()
    for sig in confirmed:
        sym = sig["symbol"]
        if sym in positions: continue
        if len(positions) >= MAX_POSITIONS:
            log(f"  ⏸ {sym} skipped (max positions)"); continue
        positions[sym] = {
            "symbol": sym, "entry": sig["entry"],
            "stop": sig["stop"], "initial_stop": sig["stop"],
            "take": sig["take"], "size": sig["size"],
            "entry_ts": now_iso, "confidence": sig["confidence"],
            "trail_updates": 0,
        }
        log(f"  🟢 {sym} OPENED LONG @ ${sig['entry']} | SL ${sig['stop']} | TP ${sig['take']}")
    state["positions"] = positions

def update_dashboard(state, latest):
    positions = state.get("positions", {})
    closed = state.get("closed", [])
    signals_all = state.get("signals", [])
    CAP_PER_POS = CAPITAL / MAX_POSITIONS

    # Live stats
    total_pnl_usd = 0; total_notional = 0
    for sym, p_data in positions.items():
        cur = next((s for s in latest if s["symbol"]==sym), None)
        if cur:
            total_pnl_usd += p_data["size"] * (cur["price"] - p_data["entry"])
            total_notional += p_data["size"] * cur["price"]
    total_lev = total_notional / CAPITAL if CAPITAL > 0 else 0

    # Historical stats
    n_closed = len(closed)
    wins = [c for c in closed if c.get("pnl_pct", 0) > 0]
    losses = [c for c in closed if c.get("pnl_pct", 0) <= 0]
    win_rate = (len(wins) / n_closed * 100) if n_closed > 0 else 0
    avg_win = sum(c["pnl_pct"] for c in wins) / len(wins) if wins else 0
    avg_loss = sum(c["pnl_pct"] for c in losses) / len(losses) if losses else 0
    total_closed_pnl = sum(c["pnl_pct"] for c in closed)

    # Signal cards (compact)
    cards = ""
    for s in latest:
        if s["signal"] == "LONG": badge, cls = '<div class="sig-badge long">LONG</div>', "sig-long"
        elif s["signal"] == "WEAK_LONG": badge, cls = '<div class="sig-badge weak">WAIT</div>', "sig-weak"
        elif s["signal"] == "OVERBOUGHT": badge, cls = '<div class="sig-badge short">OVER</div>', "sig-short"
        elif s["signal"] == "OVERSOLD": badge, cls = '<div class="sig-badge short">OVERS</div>', "sig-short"
        else: badge, cls = '<div class="sig-badge wait">WAIT</div>', "sig-wait"
        if s["confidence"] >= 80: strength, sc = "STRONG", "#3fb950"
        elif s["confidence"] >= 70: strength, sc = "MEDIUM", "#d29922"
        else: strength, sc = "WEAK", "#8b949e"
        size_usd = s["size"] * s["entry"]
        risk_usd = size_usd * (s["entry"] - s["stop"]) / s["entry"]
        reward_usd = size_usd * (s["take"] - s["entry"]) / s["entry"]
        lev = size_usd / CAP_PER_POS if CAP_PER_POS > 0 else 0
        rsi = s.get("rsi", {})
        trend = s.get("trend", {})
        up_count = sum(1 for tf in TIMEFRAMES if trend.get(tf) == "up")
        rsi_show = f"{rsi.get('15m','-')} / {rsi.get('1h','-')} / {rsi.get('4h','-')} / {rsi.get('1d','-')}"

        cards += f'''<div class="sig-card {cls}">
          <div class="sig-header"><div class="sig-symbol">{s["symbol"].replace("USDT","")}</div>{badge}</div>
          <div class="sig-price">${s["entry"]:,}</div>
          <div class="sig-strength"><span style="color:{sc};font-weight:700">{strength}</span><span style="color:{sc}">{s["confidence"]}%</span></div>
          <div class="strength-bar"><div class="strength-fill" style="width:{s["confidence"]}%;background:{sc}"></div></div>
          <div class="sig-info">
            <div class="sig-row"><span>RSI 15м/1ч/4ч/1д</span><span>{rsi_show}</span></div>
            <div class="sig-row"><span>Тренд (9 ТФ)</span><span>{up_count}/9 up</span></div>
            <div class="sig-row"><span>Плечо</span><span class="warn">{lev:.1f}x</span></div>
            <div class="sig-row"><span>Риск</span><span class="neg">-${risk_usd:,.0f}</span></div>
            <div class="sig-row"><span>Цель</span><span class="pos">+${reward_usd:,.0f}</span></div>
          </div>
        </div>'''

    # Open positions
    pos_rows = ""
    for sym, p_data in positions.items():
        cur = next((s for s in latest if s["symbol"]==sym), None)
        if not cur: continue
        pnl_pct = (cur["price"]/p_data["entry"]-1)*100
        pnl_usd = p_data["size"] * (cur["price"] - p_data["entry"])
        cls = "pos" if pnl_pct>0 else "neg" if pnl_pct<0 else ""
        notional = p_data["size"] * cur["price"]
        lev = notional / CAP_PER_POS if CAP_PER_POS > 0 else 0
        total_range = p_data["take"] - p_data["stop"]
        progress = ((cur["price"] - p_data["stop"]) / total_range * 100) if total_range > 0 else 50
        pos_rows += f'''<tr>
          <td><b>{sym.replace("USDT","")}</b></td>
          <td><span class="badge-long">LONG</span></td>
          <td>${p_data["entry"]:,}</td>
          <td class="{cls}"><b>${cur["price"]:,}</b></td>
          <td class="{cls}"><b>{pnl_pct:+.2f}%</b></td>
          <td class="{cls}"><b>${pnl_usd:+,.2f}</b></td>
          <td class="warn">{lev:.1f}x</td>
          <td class="neg">${p_data["stop"]:,}</td>
          <td class="pos">${p_data["take"]:,}</td>
          <td><div class="progress-bar"><div class="progress-stop"></div><div class="progress-now" style="left:{progress}%"></div><div class="progress-take"></div></div></td>
        </tr>'''
    if not pos_rows:
        pos_rows = '<tr><td colspan="10" style="text-align:center;color:#8b949e;padding:30px">нет открытых позиций</td></tr>'

    # Closed trades history (last 20)
    hist_rows = ""
    for c in reversed(closed[-20:]):
        cls = "pos" if c.get("pnl_pct", 0) > 0 else "neg"
        reason = c.get("exit_reason", "?")
        icon = "TAKE" if reason == "TAKE" else "STOP"
        hist_rows += f'''<tr>
          <td><b>{c["symbol"].replace("USDT","")}</b></td>
          <td>${c["entry"]:,}</td>
          <td>${c.get("exit_price", 0):,}</td>
          <td class="{cls}"><b>{c.get("pnl_pct", 0):+.2f}%</b></td>
          <td class="{'pos' if reason=='TAKE' else 'neg'}">{icon}</td>
          <td style="font-size:11px;color:#8b949e">{c.get("entry_ts","")[:16]}</td>
        </tr>'''
    if not hist_rows:
        hist_rows = '<tr><td colspan="6" style="text-align:center;color:#8b949e;padding:20px">сделок ещё нет — ждём первый сигнал</td></tr>'

    charts_json = json.dumps({s["symbol"]: s.get("charts", {}) for s in latest})
    pnl_cls = "pos" if total_pnl_usd > 0 else "neg" if total_pnl_usd < 0 else ""
    now_ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Crypto Signals</title><meta http-equiv="refresh" content="30">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;padding:20px;font-size:14px}}
h1{{color:#58a6ff;margin-bottom:6px;font-size:22px}}
h2{{color:#58a6ff;margin:28px 0 12px;font-size:17px}}
.time{{color:#8b949e;font-size:12px;margin-bottom:20px}}
.total-header{{background:linear-gradient(135deg,#161b22,#1c2128);border:2px solid #30363d;border-radius:12px;padding:22px;margin-bottom:20px;text-align:center}}
.total-label{{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:1px}}
.total-value{{font-size:44px;font-weight:800;margin:8px 0;font-family:monospace}}
.total-value.pos{{color:#3fb950;text-shadow:0 0 20px rgba(63,185,80,0.3)}}
.total-value.neg{{color:#f85149;text-shadow:0 0 20px rgba(248,81,73,0.3)}}
.total-sub{{color:#8b949e;font-size:14px}}
.top-stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:20px}}
.stat{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;text-align:center}}
.stat .lbl{{color:#8b949e;font-size:11px;text-transform:uppercase}}
.stat .val{{font-size:20px;font-weight:700;margin-top:6px}}
.sig-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin:16px 0}}
.sig-card{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:16px}}
.sig-long{{border-left:4px solid #3fb950;background:linear-gradient(135deg,#161b22,rgba(63,185,80,0.08))}}
.sig-weak{{border-left:4px solid #d29922}}
.sig-short{{border-left:4px solid #f85149}}
.sig-wait{{border-left:4px solid #30363d;opacity:0.75}}
.sig-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.sig-symbol{{font-size:20px;font-weight:800;color:#fff}}
.sig-badge{{padding:4px 10px;border-radius:6px;font-size:11px;font-weight:700}}
.sig-badge.long{{background:rgba(63,185,80,0.25);color:#3fb950;border:1px solid #3fb950}}
.sig-badge.short{{background:rgba(248,81,73,0.25);color:#f85149;border:1px solid #f85149}}
.sig-badge.weak{{background:rgba(210,153,34,0.25);color:#d29922;border:1px solid #d29922}}
.sig-badge.wait{{background:rgba(139,148,158,0.15);color:#8b949e;border:1px solid #30363d}}
.sig-price{{font-size:24px;font-weight:800;color:#fff;font-family:monospace;margin-bottom:10px}}
.sig-strength{{display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px}}
.strength-bar{{height:6px;background:#21262d;border-radius:3px;overflow:hidden;margin-bottom:12px}}
.strength-fill{{height:100%}}
.sig-info{{font-size:12px}}
.sig-row{{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #21262d}}
.sig-row:last-child{{border-bottom:none}}
.sig-row span:first-child{{color:#8b949e}}
table{{width:100%;border-collapse:collapse;margin:12px 0;background:#161b22;border-radius:10px;overflow:hidden;font-size:13px}}
th{{color:#8b949e;font-size:11px;text-transform:uppercase;background:#1c2128;padding:10px 8px;text-align:right;font-weight:600}}
td{{padding:12px 8px;text-align:right;border-bottom:1px solid #21262d}}
th:first-child,td:first-child{{text-align:left}}
.pos{{color:#3fb950;font-weight:700}}.neg{{color:#f85149;font-weight:700}}.warn{{color:#d29922;font-weight:700}}
.badge-long{{background:rgba(63,185,80,0.15);color:#3fb950;padding:3px 8px;border-radius:5px;font-size:11px;font-weight:700;border:1px solid #3fb950}}
.progress-bar{{position:relative;height:6px;background:#21262d;border-radius:3px;width:70px;display:inline-block}}
.progress-stop{{position:absolute;left:0;top:0;width:3px;height:6px;background:#f85149}}
.progress-take{{position:absolute;right:0;top:0;width:3px;height:6px;background:#3fb950}}
.progress-now{{position:absolute;top:-3px;width:2px;height:12px;background:#58a6ff;box-shadow:0 0 6px #58a6ff}}
.chart-tabs{{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}}
.tf-tabs{{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}}
.tf-tab{{padding:6px 14px;background:#21262d;border:1px solid #30363d;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600}}
.tf-tab.active{{background:#1f6feb;color:#fff}}
.chart-tab{{padding:8px 16px;background:#21262d;border:1px solid #30363d;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600}}
.chart-tab:hover{{background:#30363d}}
.chart-tab.active{{background:#1f6feb;color:#fff;border-color:#58a6ff}}
.chart-box{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;height:420px}}
canvas{{max-height:380px !important;height:380px !important}}
</style></head><body>

<h1>🎯 Crypto Signals — LIVE</h1>
<div class="time">Обновлено: {now_ts} | авто-refresh 30s | цикл #{state["cycle"]}</div>

<div class="total-header">
  <div class="total-label">💼 ПРИБЫЛЬ ПОРТФЕЛЯ СЕЙЧАС</div>
  <div class="total-value {pnl_cls}">${total_pnl_usd:+,.2f}</div>
  <div class="total-sub">{len(positions)}/{MAX_POSITIONS} позиций | общее плечо {total_lev:.1f}x</div>
</div>

<div class="top-stats">
  <div class="stat"><div class="lbl">Позиции</div><div class="val">{len(positions)}/{MAX_POSITIONS}</div></div>
  <div class="stat"><div class="lbl">Всего сигналов</div><div class="val pos">{len(signals_all)}</div></div>
  <div class="stat"><div class="lbl">Закрыто</div><div class="val">{n_closed}</div></div>
  <div class="stat"><div class="lbl">Win Rate</div><div class="val {'pos' if win_rate>50 else 'neg' if n_closed>0 else ''}">{win_rate:.0f}%</div></div>
  <div class="stat"><div class="lbl">Ср. прибыль</div><div class="val pos">{avg_win:+.2f}%</div></div>
  <div class="stat"><div class="lbl">Ср. убыток</div><div class="val neg">{avg_loss:+.2f}%</div></div>
</div>

<h2>📊 Сигналы сейчас</h2>
<div class="sig-grid">{cards}</div>

<h2>💼 Открытые позиции</h2>
<table>
<tr><th>Монета</th><th>Тип</th><th>Вход</th><th>Сейчас</th><th>PnL %</th><th>PnL $</th><th>Плечо</th><th>Stop</th><th>Take</th><th>Прогресс</th></tr>
{pos_rows}
</table>

<h2>📜 История закрытых сделок ({n_closed})</h2>
<table>
<tr><th>Монета</th><th>Вход</th><th>Выход</th><th>PnL %</th><th>Причина</th><th>Дата входа</th></tr>
{hist_rows}
</table>

<h2>📈 Графики — 9 таймфреймов</h2>
<div class="tf-tabs" id="tfTabs">
  <div class="tf-tab active" data-tf="15m">15м</div>
  <div class="tf-tab" data-tf="1h">1ч</div>
  <div class="tf-tab" data-tf="4h">4ч</div>
  <div class="tf-tab" data-tf="6h">6ч</div>
  <div class="tf-tab" data-tf="12h">12ч</div>
  <div class="tf-tab" data-tf="1d">1д</div>
  <div class="tf-tab" data-tf="3d">3д</div>
  <div class="tf-tab" data-tf="1w">1н</div>
  <div class="tf-tab" data-tf="1M">1М</div>
</div>
<div class="chart-tabs" id="tabs"></div>
<div class="chart-box"><canvas id="mainChart"></canvas></div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
const CHARTS = {charts_json};
let currentChart = null, currentSym = null, currentTF = '15m';
function renderChart() {{
  if (!currentSym) return;
  const data = CHARTS[currentSym] && CHARTS[currentSym][currentTF];
  if (!data) return;
  document.querySelectorAll('.chart-tab').forEach(t => t.classList.toggle('active', t.dataset.sym === currentSym));
  document.querySelectorAll('.tf-tab').forEach(t => t.classList.toggle('active', t.dataset.tf === currentTF));
  const ctx = document.getElementById('mainChart').getContext('2d');
  if (currentChart) currentChart.destroy();
  const n = data.times.length;
  const cLine = (val, color, label, dash) => ({{ label, data: new Array(n).fill(val), borderColor: color, borderWidth: 2, borderDash: dash || [], pointRadius: 0, fill: false, tension: 0 }});
  const entryPt = {{ label: 'Entry', data: data.close.map((v,i)=>i===n-1?data.entry:null), borderColor: '#fff', backgroundColor: '#fff', pointRadius: 10, pointStyle: 'triangle', showLine: false }};
  currentChart = new Chart(ctx, {{
    type: 'line',
    data: {{ labels: data.times, datasets: [
      {{ label: currentSym.replace('USDT','') + ' Price', data: data.close, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)', fill: true, tension: 0.1, pointRadius: 0, borderWidth: 2, order: 10 }},
      {{ label: 'EMA20', data: data.ema20, borderColor: '#3fb950', borderWidth: 1.5, pointRadius: 0, order: 9 }},
      {{ label: 'EMA50', data: data.ema50, borderColor: '#d29922', borderWidth: 1.5, pointRadius: 0, order: 9 }},
      cLine(data.entry, '#ffffff', 'Entry $' + data.entry, [6,4]),
      cLine(data.take, '#3fb950', 'TP $' + data.take, [4,4]),
      cLine(data.stop, '#f85149', 'SL $' + data.stop, [4,4]),
      entryPt
    ]}},
    options: {{ responsive: true, maintainAspectRatio: false, animation: {{ duration: 0 }},
      plugins: {{ legend: {{ labels: {{ color: '#c9d1d9', font: {{ size: 12 }} }} }}, title: {{ display: true, text: currentSym + ' — ' + currentTF, color: '#58a6ff', font: {{ size: 14 }} }} }},
      scales: {{ x: {{ ticks: {{ maxTicksLimit: 8, color: '#8b949e', font: {{ size: 10 }} }}, grid: {{ color: '#21262d' }} }}, y: {{ ticks: {{ color: '#8b949e', font: {{ size: 10 }} }}, grid: {{ color: '#21262d' }} }} }} }}
  }});
}}
document.querySelectorAll('.tf-tab').forEach(t => {{ t.onclick = () => {{ currentTF = t.dataset.tf; renderChart(); }}; }});
const tabs = document.getElementById('tabs');
Object.keys(CHARTS).forEach(sym => {{
  const t = document.createElement('div');
  t.className = 'chart-tab'; t.dataset.sym = sym; t.textContent = sym.replace('USDT','');
  t.onclick = () => {{ currentSym = sym; renderChart(); }};
  tabs.appendChild(t);
}});
if (Object.keys(CHARTS).length > 0) {{ currentSym = Object.keys(CHARTS)[0]; renderChart(); }}
</script>
</body></html>"""
    (DASH/"live_stats.html").write_text(html)

def one_cycle(ex, state):
    state["cycle"] = state.get("cycle",0) + 1
    latest = []; new_confirmed = []
    for sym in SYMBOLS:
        try:
            time.sleep(1.5)
            sig = compute_mtf_signal(ex, sym)
            if not sig: continue
            latest.append(sig)
            rsi_short = f"{sig['rsi']['15m']}/{sig['rsi']['1h']}/{sig['rsi']['4h']}/{sig['rsi']['1d']}"
            mark = "✅" if sig["signal"]=="LONG" else "⚠️" if sig["signal"]=="WEAK_LONG" else " "
            log(f"  {mark} {sym}: {sig['signal']:<12} conf={sig['confidence']}% RSI={rsi_short}")
            if sig["signal"] == "LONG":
                with SIGNALS_LOG.open("a") as f:
                    f.write(json.dumps({k:v for k,v in sig.items() if k!="charts"}) + "\n")
                recent = [s for s in state["signals"]
                          if s["symbol"]==sym and
                          (datetime.fromisoformat(sig["ts"]) - datetime.fromisoformat(s["ts"])).total_seconds() < 21600]
                if not recent:
                    state["signals"].append({k:v for k,v in sig.items() if k!="charts"})
                    new_confirmed.append(sig)
        except Exception as e:
            log(f"  {sym} ERROR: {e}")
    update_positions(state, latest)
    if new_confirmed:
        open_new_positions(state, new_confirmed)
    update_dashboard(state, latest)
    save_state(state)
    if new_confirmed:
        msg = f"<b>🚨 v7 SIGNAL (9 TF)</b>\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
        for s in new_confirmed:
            msg += f"<b>{s['symbol']}</b> LONG (conf {s['confidence']}%)\n"
            msg += f"  Entry: <code>${s['entry']}</code>\n"
            msg += f"  Stop:  <code>${s['stop']}</code>\n"
            msg += f"  Take:  <code>${s['take']}</code>\n\n"
        tg_send(msg)
    if state["cycle"] % PUSH_EVERY_N == 0:
        if git_push():
            state["pushes"] = state.get("pushes",0) + 1
            save_state(state)

def main():
    log("LIVE COLLECTOR v7 STARTED (9 timeframes)")
    log(f"Symbols: {len(SYMBOLS)} | TFs: {len(TIMEFRAMES)} | Poll: {POLL_SEC}s | Conf: {MIN_CONFIDENCE}")
    ex = ccxt.binance({"enableRateLimit": True})
    state = load_state()
    log(f"State: cycle={state.get('cycle',0)} signals={len(state.get('signals',[]))}")
    while True:
        try:
            one_cycle(ex, state)
        except Exception as e:
            log(f"CYCLE ERROR: {e}")
        log(f"Sleep {POLL_SEC}s...\n")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
