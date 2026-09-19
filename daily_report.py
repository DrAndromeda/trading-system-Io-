"""
DAILY REPORT - Telegram + Charts + RDR
======================================
Sends to Telegram at 06:00:
  1. Equity chart (PNG)
  2. Signal map (PNG)
  3. Text: R:R, P&L, orders
"""
import json, os, warnings
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.parse import urlencode

warnings.filterwarnings("ignore")
DATA = Path("data"); LOGS = Path("logs"); DASH = Path("dashboard")
for p in [LOGS, DASH]: p.mkdir(exist_ok=True)
STATE = Path("paper_state.json")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]

# ---------- DATA ----------
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

# ---------- INDICATORS ----------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rsi(s, n=14):
    d = s.diff(); g = d.clip(lower=0).rolling(n).mean(); l = -d.clip(upper=0).rolling(n).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))
def atr(df, n=14):
    h,l,c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# ---------- SIGNALS ----------
def compute_signals(sym):
    df1h = load_1h(sym)
    if df1h is None: return None
    dfd = to_daily(df1h)
    d = dfd.copy()
    d["ema20"] = ema(d["close"],20); d["ema50"] = ema(d["close"],50); d["ema200"] = ema(d["close"],200)
    d["rsi"] = rsi(d["close"],14); d["atr"] = atr(d,14)
    d["atr_pct"] = d["atr"]/d["close"]*100
    d["signal"] = "WAIT"
    long_c = (d["ema20"]>d["ema50"]) & (d["rsi"]>50) & (d["rsi"]<75)
    d.loc[long_c,"signal"] = "LONG"
    d.loc[d["rsi"]>80,"signal"] = "WAIT"
    d.loc[d["rsi"]<20,"signal"] = "WAIT"

    last = d.iloc[-1]
    if last["signal"] not in ("LONG","SHORT"): return None
    entry = float(last["close"]); atr_v = float(last["atr"])
    if last["signal"] == "LONG":
        stop = entry - 2*atr_v; take = entry + 5*atr_v
    else:
        stop = entry + 2*atr_v; take = entry - 5*atr_v
    rr = abs(take-entry) / abs(entry-stop) if abs(entry-stop) > 0 else 0

    # Funding
    f = load_funding(sym)
    fund_ann = float(f.rolling(7).mean().iloc[-1]) * 3 * 365 * 100 if f is not None and len(f) > 30 else 0

    return {
        "symbol": sym, "date": str(last.name.date()),
        "signal": last["signal"], "entry": round(entry,4),
        "stop": round(stop,4), "take": round(take,4),
        "rsi": round(float(last["rsi"]),1), "atr_pct": round(float(last["atr_pct"]),2),
        "rr": round(rr,2), "funding_ann": round(fund_ann,2),
        "sl_pct": round(abs(entry-stop)/entry*100,2),
        "tp_pct": round(abs(take-entry)/entry*100,2),
    }

# ---------- STATE ----------
def load_state():
    if STATE.exists(): return json.loads(STATE.read_text())
    return {"first_run": None, "signals": [], "runs": 0}

def save_state(s):
    STATE.write_text(json.dumps(s, indent=2, default=str))

# ---------- CHARTS ----------
def make_signals_chart(signals):
    if not signals: return None
    fig, axes = plt.subplots(1, len(signals), figsize=(4*len(signals), 4), facecolor="#0d1117")
    if len(signals) == 1: axes = [axes]
    for ax, s in zip(axes, signals):
        ax.set_facecolor("#161b22")
        entry, stop, take = s["entry"], s["stop"], s["take"]
        ymin = min(stop, take) * 0.998
        ymax = max(stop, take) * 1.002
        ax.add_patch(Rectangle((0.1, stop), 0.8, entry-stop, alpha=0.3, color="#f85149"))
        ax.add_patch(Rectangle((0.1, entry), 0.8, take-entry, alpha=0.3, color="#3fb950"))
        ax.axhline(entry, color="#58a6ff", linewidth=2, label="Entry")
        ax.axhline(stop, color="#f85149", linewidth=1, linestyle="--", label="Stop")
        ax.axhline(take, color="#3fb950", linewidth=1, linestyle="--", label="Take")
        ax.set_xlim(0, 1); ax.set_ylim(ymin, ymax)
        ax.set_title(f"{s['symbol']}\n{s['signal']} | R:R {s['rr']}", color="#c9d1d9", fontsize=11)
        ax.tick_params(colors="#8b949e", labelsize=8)
        for spine in ax.spines.values(): spine.set_color("#30363d")
        ax.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="#c9d1d9", fontsize=8, loc="best")
    plt.tight_layout()
    p = DASH/"signals_chart.png"
    plt.savefig(p, dpi=100, facecolor="#0d1117")
    plt.close()
    return p

def make_equity_chart():
    # Простой: carry equity по всем 20 символам
    all_syms = [f.stem.replace("_funding","") for f in (DATA/"raw/binance").glob("*_funding.parquet")]
    rets = []
    for s in all_syms:
        f = load_funding(s)
        if f is None: continue
        r = f.where(f>0, 0.0) * 3 / 1.5
        rets.append(r)
    if not rets: return None
    idx = None
    for r in rets: idx = r.index if idx is None else idx.union(r.index)
    port = pd.concat([r.reindex(idx).fillna(0) for r in rets], axis=1).mean(axis=1)
    cum = (1+port).cumprod()
    dd = (cum/cum.cummax()-1)*100

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 6), facecolor="#0d1117",
                                    gridspec_kw={"height_ratios":[3,1]})
    for ax in (ax1, ax2): ax.set_facecolor("#161b22")
    ax1.plot(cum.index, cum.values, color="#58a6ff", linewidth=2)
    ax1.fill_between(cum.index, 1, cum.values, color="#58a6ff", alpha=0.15)
    ax1.set_title("Carry Portfolio Equity", color="#c9d1d9", fontsize=13)
    ax1.tick_params(colors="#8b949e"); ax1.grid(True, alpha=0.1, color="#8b949e")
    for spine in ax1.spines.values(): spine.set_color("#30363d")
    ax2.fill_between(dd.index, 0, dd.values, color="#f85149", alpha=0.4)
    ax2.plot(dd.index, dd.values, color="#f85149", linewidth=1)
    ax2.set_title("Drawdown %", color="#c9d1d9", fontsize=11)
    ax2.tick_params(colors="#8b949e"); ax2.grid(True, alpha=0.1, color="#8b949e")
    for spine in ax2.spines.values(): spine.set_color("#30363d")
    plt.tight_layout()
    p = DASH/"equity_chart.png"
    plt.savefig(p, dpi=100, facecolor="#0d1117")
    plt.close()
    return p

# ---------- TELEGRAM ----------
def tg_send_text(text):
    if not TG_TOKEN or not TG_CHAT:
        print(f"  [TG DRY-RUN] {text[:150]}")
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = urlencode({"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"}).encode()
        with urlopen(Request(url, data=data), timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"  [TG TEXT ERR] {e}"); return False

def tg_send_photo(photo_path, caption=""):
    if not TG_TOKEN or not TG_CHAT:
        print(f"  [TG DRY] photo: {photo_path}")
        return False
    try:
        import mimetypes
        boundary = "----PyBoundary7MA4YWxkTrZu0gW"
        with open(photo_path, "rb") as f: img = f.read()
        body = b""
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        body += f"{TG_CHAT}\r\n".encode()
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="caption"\r\n\r\n'
        body += caption.encode("utf-8") + b"\r\n"
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
        body += b"Content-Type: image/png\r\n\r\n"
        body += img + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
        req = Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception as e:
        print(f"  [TG PHOTO ERR] {e}"); return False

# ---------- MAIN ----------
def main():
    print("="*100)
    print("  DAILY REPORT")
    print("="*100)
    now = datetime.now(timezone.utc)
    print(f"  Time: {now.strftime('%Y-%m-%d %H:%M UTC')}")

    state = load_state()
    if not state.get("first_run"):
        state["first_run"] = now.isoformat()
    if "signals" not in state or not isinstance(state["signals"], list):
        state["signals"] = []
    state["runs"] = state.get("runs", 0) + 1

    # Signals
    signals = []
    for sym in SYMBOLS:
        s = compute_signals(sym)
        if s: signals.append(s); print(f"  {sym}: {s['signal']} entry={s['entry']} RR={s['rr']} SL={s['sl_pct']}% TP={s['tp_pct']}%")

    # Save to state
    for s in signals:
        s["captured_at"] = now.isoformat()
        state["signals"].append(s)
    save_state(state)

    # Charts
    print("\n  Building charts...")
    eq_chart = make_equity_chart()
    sig_chart = make_signals_chart(signals)
    print(f"    equity: {eq_chart}")
    print(f"    signals: {sig_chart}")

    # Text message
    lines = [f"<b>📊 Daily Report</b>", now.strftime("%Y-%m-%d %H:%M UTC"), ""]
    lines.append(f"<b>Active signals:</b> {len(signals)}")
    for s in signals:
        emoji = "🟢" if s["signal"]=="LONG" else "🔴"
        lines.append(f"{emoji} <b>{s['symbol']}</b> @ {s['entry']}")
        lines.append(f"   SL {s['stop']} ({s['sl_pct']}%) | TP {s['take']} ({s['tp_pct']}%)")
        lines.append(f"   R:R <b>{s['rr']}</b> | RSI {s['rsi']} | funding {s['funding_ann']:+.1f}%")
    lines.append("")
    lines.append(f"<b>System:</b> run #{state['runs']} | first {state['first_run'][:10]}")
    lines.append(f"<b>Signals logged:</b> {len(state['signals'])}")
    text = "\n".join(lines)

    # Send
    print("\n  Sending Telegram...")
    ok_text = tg_send_text(text)
    print(f"    text: {'sent' if ok_text else 'dry-run'}")
    if eq_chart:
        ok1 = tg_send_photo(eq_chart, "Equity + Drawdown")
        print(f"    equity chart: {'sent' if ok1 else 'dry-run'}")
    if sig_chart:
        ok2 = tg_send_photo(sig_chart, "Signal map (SL/TP levels)")
        print(f"    signal chart: {'sent' if ok2 else 'dry-run'}")

    print("\n" + "="*100)
    print("  DONE")
    print("="*100)

if __name__ == "__main__":
    main()
