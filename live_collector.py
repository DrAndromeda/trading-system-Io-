"""
LIVE COLLECTOR v2 — HIGH CONFIDENCE SIGNALS ONLY
=================================================
Фильтры для LONG (нужны ВСЕ условия):
  1. EMA20 > EMA50 (тренд вверх)
  2. 50 < RSI < 68 (не перекуплен)
  3. Цена > SMA100 (в долгосрочном аптренде)
  4. ATR% < 3 (не экстремальная волатильность)
  5. RSI растёт (RSI[0] > RSI[5])
  6. Volume > SMA(volume, 20) (подтверждение)
Confidence score 0-100, отправляем только > 75

Авто-push в GitHub каждый час (12 циклов).
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
LIVE_LOG = LOGS/"live.jsonl"
SIGNALS_LOG = LOGS/"signals_confirmed.jsonl"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]
RR = 2.5
SL_ATR = 2.0
CAPITAL = 10000.0
RISK_PCT = 1.0
POLL_SEC = 300
PUSH_EVERY_N = 12      # каждые 12 циклов (1 час) → git push
MIN_CONFIDENCE = 75    # только сигналы > 75

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

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
        log(f"TG error: {e}"); return False

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
        if "signals" not in s: s["signals"] = []
        if "pushes" not in s: s["pushes"] = 0
        if "cycle" not in s: s["cycle"] = 0
        return s
    return {"signals": [], "pushes": 0, "cycle": 0, "started": datetime.now(timezone.utc).isoformat()}

def save_state(s):
    STATE.write_text(json.dumps(s, indent=2, default=str))

def git_push():
    """Auto-push logs to GitHub."""
    try:
        cmds = [
            ["git", "add", "-A"],
            ["git", "commit", "-m", f"auto: signals {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
            ["git", "push", "origin", "main"],
        ]
        for c in cmds:
            r = subprocess.run(c, cwd=ROOT, capture_output=True, text=True, timeout=30)
            out = (r.stdout + r.stderr).strip()
            if r.returncode != 0 and "nothing to commit" not in out and "up-to-date" not in out:
                log(f"git: {out[:120]}")
                return False
        log("✅ pushed to GitHub")
        return True
    except Exception as e:
        log(f"git error: {e}")
        return False

def fetch_data(ex, sym):
    ohlcv = ex.fetch_ohlcv(sym, "1h", limit=200)
    df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_localize(None)
    df = df.set_index("ts")
    try:
        fr = ex.fetch_funding_rate(sym)
        funding = fr.get("fundingRate", 0) or 0
    except Exception:
        funding = 0
    return df, funding

def compute_signal(df, funding, sym):
    """Строгий сигнал с confidence score."""
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

    # Conditions
    trend_up = last["ema20"] > last["ema50"]
    above_sma = last["close"] > last["sma100"]
    rsi_ok = 50 < last["rsi"] < 68
    rsi_rising = last["rsi"] > prev_rsi
    atr_ok = last["atr_pct"] < 3.0
    volume_ok = last["volume"] > last["vol_sma20"]

    # Signal
    signal = "WAIT"
    if trend_up and rsi_ok and above_sma and atr_ok:
        if rsi_rising and volume_ok:
            signal = "LONG"
        else:
            signal = "WEAK_LONG"
    elif last["rsi"] > 75:
        signal = "OVERBOUGHT"
    elif last["rsi"] < 30:
        signal = "OVERSOLD"

    # Confidence
    conf = 0
    if trend_up: conf += 20
    if above_sma: conf += 15
    if rsi_ok: conf += 20
    if rsi_rising: conf += 15
    if atr_ok: conf += 10
    if volume_ok: conf += 10
    if funding > 0: conf += 5
    if funding * 3 * 365 * 100 > 10: conf += 5

    entry = float(last["close"])
    atr_v = float(last["atr"])
    if atr_v > 0:
        stop = entry - SL_ATR*atr_v
        take = entry + SL_ATR*atr_v*RR
        size = (CAPITAL*RISK_PCT/100) / (SL_ATR*atr_v)
    else:
        stop = take = entry; size = 0

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": sym,
        "signal": signal,
        "confidence": conf,
        "price": round(entry, 6),
        "ema20": round(float(last["ema20"]),4),
        "ema50": round(float(last["ema50"]),4),
        "sma100": round(float(last["sma100"]),4),
        "rsi": round(float(last["rsi"]),1),
        "rsi_rising": bool(rsi_rising),
        "atr_pct": round(float(last["atr_pct"]),2),
        "volume_ok": bool(volume_ok),
        "entry": round(entry, 6),
        "stop": round(stop, 6),
        "take": round(take, 6),
        "size": round(size, 6),
        "rr": RR,
        "funding_ann": round(funding * 3 * 365 * 100, 2),
    }

def update_dashboard(state, latest):
    rows = ""
    for s in latest:
        cls = "pos" if s["signal"]=="LONG" else "neg" if s["signal"]=="OVERBOUGHT" else ""
        conf_bar = "#" * int(s["confidence"]/10)
        rows += f'''<tr>
          <td><b>{s['symbol']}</b></td>
          <td>${s['price']:,}</td>
          <td>{s['rsi']}</td>
          <td>{s['atr_pct']}%</td>
          <td class="{cls}"><b>{s['signal']}</b></td>
          <td>{s['confidence']}% <span style="color:#3fb950">{conf_bar}</span></td>
          <td class="neg">${s['stop']:,}</td>
          <td class="pos">${s['take']:,}</td>
          <td>{s['size']}</td>
        </tr>'''

    confirmed = ""
    if SIGNALS_LOG.exists():
        lines = SIGNALS_LOG.read_text().strip().split("\n")[-20:]
        for line in reversed(lines):
            try:
                ev = json.loads(line)
                confirmed += f'''<tr><td>{ev['ts'][:19]}</td><td><b>{ev['symbol']}</b></td>
                  <td>${ev['price']}</td><td>{ev['rsi']}</td><td>{ev['confidence']}%</td>
                  <td class="neg">${ev['stop']}</td><td class="pos">${ev['take']}</td></tr>'''
            except: pass

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>LIVE Confirmed Signals</title><meta http-equiv="refresh" content="60">
<style>
*{{box-sizing:border-box}}body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;margin:0;padding:20px}}
h1{{color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px}}
h2{{color:#58a6ff;margin-top:30px}}.time{{color:#8b949e;font-size:13px;margin-bottom:15px}}
.live-dot{{display:inline-block;width:10px;height:10px;background:#3fb950;border-radius:50%;animation:p 2s infinite;margin-right:8px}}
@keyframes p{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
table{{width:100%;border-collapse:collapse;margin:15px 0;font-size:14px}}
th,td{{padding:10px 12px;text-align:right;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-size:11px;text-transform:uppercase;background:#161b22}}
th:first-child,td:first-child{{text-align:left}}
tr:hover{{background:#161b22}}.pos{{color:#3fb950}}.neg{{color:#f85149}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px;margin:20px 0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px}}
.card .label{{color:#8b949e;font-size:11px;text-transform:uppercase}}
.card .value{{font-size:22px;font-weight:700;margin-top:6px}}
.info{{background:#161b22;border-left:3px solid #58a6ff;padding:12px;border-radius:6px;margin:10px 0;font-size:13px}}
</style></head><body>
<h1><span class="live-dot"></span>LIVE Confirmed Signals (confidence > 75)</h1>
<div class="time">Last: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | refresh 60s</div>
<div class="info"><b>Filters:</b> EMA20>EMA50 | RSI 50-68 rising | Price>SMA100 | ATR%<3 | Volume>avg | Confidence>75</div>
<div class="grid">
  <div class="card"><div class="label">Cycle</div><div class="value">{state['cycle']}</div></div>
  <div class="card"><div class="label">Confirmed</div><div class="value pos">{len(state['signals'])}</div></div>
  <div class="card"><div class="label">Active LONG</div><div class="value pos">{sum(1 for s in latest if s['signal']=='LONG')}</div></div>
  <div class="card"><div class="label">Git pushes</div><div class="value">{state.get('pushes',0)}</div></div>
</div>
<h2>Live Status</h2>
<table><tr><th>Symbol</th><th>Price</th><th>RSI</th><th>ATR%</th><th>Signal</th><th>Confidence</th><th>Stop</th><th>Take</th><th>Size</th></tr>
{rows}</table>
<h2>Confirmed Signals History</h2>
<table><tr><th>Time</th><th>Symbol</th><th>Price</th><th>RSI</th><th>Conf</th><th>Stop</th><th>Take</th></tr>
{confirmed if confirmed else '<tr><td colspan="7" style="text-align:center;color:#8b949e">waiting for first high-confidence signal...</td></tr>'}</table>
</body></html>"""
    (DASH/"live_stats.html").write_text(html)

def one_cycle(ex, state):
    state["cycle"] = state.get("cycle",0) + 1
    latest = []
    new_confirmed = []

    for sym in SYMBOLS:
        try:
            df, funding = fetch_data(ex, sym)
            sig = compute_signal(df, funding, sym)
            latest.append(sig)

            mark = "✅" if sig["signal"]=="LONG" else "⚠️" if sig["signal"]=="WEAK_LONG" else " "
            log(f"  {mark} {sym}: {sig['signal']:<12} conf={sig['confidence']}% RSI={sig['rsi']} ${sig['price']}")

            if sig["signal"] == "LONG" and sig["confidence"] >= MIN_CONFIDENCE:
                with SIGNALS_LOG.open("a") as f:
                    f.write(json.dumps(sig) + "\n")
                recent = [s for s in state["signals"]
                          if s["symbol"]==sym and
                          (datetime.fromisoformat(sig["ts"]) - datetime.fromisoformat(s["ts"])).total_seconds() < 21600]
                if not recent:
                    state["signals"].append(sig)
                    new_confirmed.append(sig)
        except Exception as e:
            log(f"  {sym} ERROR: {e}")

    update_dashboard(state, latest)
    save_state(state)

    # Telegram
    if new_confirmed:
        msg = f"<b>🚨 CONFIRMED SIGNAL</b>\n{datetime.now(timezone.utc).strftime('%H:%M UTC')}\n\n"
        for s in new_confirmed:
            msg += f"<b>{s['symbol']}</b> LONG (conf {s['confidence']}%)\n"
            msg += f"  Entry: <code>${s['entry']}</code>\n"
            msg += f"  Stop:  <code>${s['stop']}</code>\n"
            msg += f"  Take:  <code>${s['take']}</code>\n"
            msg += f"  Size:  {s['size']}\n"
            msg += f"  RSI:   {s['rsi']}\n\n"
        tg_send(msg)
        log(f"  📱 Telegram sent: {len(new_confirmed)} signals")

    # Auto-push every N cycles
    if state["cycle"] % PUSH_EVERY_N == 0:
        log(f"  📤 Auto-push (cycle {state['cycle']})...")
        if git_push():
            state["pushes"] = state.get("pushes",0) + 1
            save_state(state)

def main():
    log("LIVE COLLECTOR v2 STARTED (confidence filter)")
    log(f"Symbols: {SYMBOLS} | Poll: {POLL_SEC}s | Push: every {PUSH_EVERY_N} cycles | Conf threshold: {MIN_CONFIDENCE}")
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
