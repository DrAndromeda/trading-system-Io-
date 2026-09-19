"""
TEST ORDERS - визуализация боевых ордеров
==========================================
Показывает как будут выглядеть реальные ордера:
  - Entry / SL / TP
  - Size в монетах и в $
  - Risk / Reward в $
  - R:R ratio
  - Expected P&L scenarios
"""
import json, warnings, webbrowser
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data"); DASH = Path("dashboard"); LOGS = Path("logs")
for p in [DASH, LOGS]: p.mkdir(exist_ok=True)
W = 100

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]
CAPITAL = 10000.0   # общий капитал
RISK_PCT = 1.0      # риск на сделку 1%

def panel(t): print("="*W); print("  "+t); print("="*W)

def load_1h(sym):
    p = DATA/f"cache/{sym}_1h_20000.npz"
    if not p.exists(): return None
    d = np.load(p); n = len(d["c"])
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
    d = s.diff(); g = d.clip(lower=0).rolling(n).mean(); l = -d.clip(upper=0).rolling(n).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))
def atr(df, n=14):
    h,l,c = df["high"], df["low"], df["close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def build_order(sym):
    """Строит боевой ордер для символа."""
    df1h = load_1h(sym)
    if df1h is None: return None
    dfd = to_daily(df1h)
    d = dfd.copy()
    d["ema20"] = ema(d["close"],20); d["ema50"] = ema(d["close"],50)
    d["rsi"] = rsi(d["close"],14); d["atr"] = atr(d,14)
    d["atr_pct"] = d["atr"]/d["close"]*100

    last = d.iloc[-1]
    trend = "LONG" if last["ema20"] > last["ema50"] else "SHORT"
    if not (50 < last["rsi"] < 75): return None

    entry = float(last["close"])
    atr_v = float(last["atr"])
    sl_dist = 2.0 * atr_v
    tp_dist = 5.0 * atr_v   # R:R = 2.5

    if trend == "LONG":
        stop = entry - sl_dist
        take = entry + tp_dist
    else:
        stop = entry + sl_dist
        take = entry - tp_dist

    risk_usd = CAPITAL * RISK_PCT / 100
    size = risk_usd / sl_dist  # размер в монетах

    # Expected P&L сценарии
    pnl_stop = -risk_usd
    pnl_take = tp_dist * size
    pnl_if_flat = 0

    return {
        "symbol": sym,
        "action": trend,
        "entry": entry,
        "stop": stop,
        "take": take,
        "size": size,
        "notional": size * entry,
        "risk_usd": risk_usd,
        "reward_usd": pnl_take,
        "sl_pct": sl_dist / entry * 100,
        "tp_pct": tp_dist / entry * 100,
        "rr": tp_dist / sl_dist,
        "rsi": float(last["rsi"]),
        "atr_pct": float(last["atr_pct"]),
        "date": str(last.name.date()),
    }

def render_order(order):
    """Красивый вывод одного ордера."""
    o = order
    action_emoji = "[LONG]" if o["action"] == "LONG" else "[SHORT]"
    print(f"\n  {action_emoji} {o['symbol']}  ({o['date']})")
    print(f"  " + "-"*70)
    print(f"  Entry:        ${o['entry']:>12,.4f}")
    print(f"  Stop Loss:    ${o['stop']:>12,.4f}   ({o['sl_pct']:+.2f}%)")
    print(f"  Take Profit:  ${o['take']:>12,.4f}   ({o['tp_pct']:+.2f}%)")
    print(f"  Size:          {o['size']:>12.6f}  ({o['notional']:>10,.2f} USD notional)")
    print(f"  Risk:         ${o['risk_usd']:>12,.2f}   (if stop hits)")
    print(f"  Reward:       ${o['reward_usd']:>12,.2f}   (if take hits)")
    print(f"  R:R ratio:    {o['rr']:>12.2f}   (>1.5 = good, >2.0 = great)")
    print(f"  RSI:          {o['rsi']:>12.1f}   (50-75 = ideal)")
    print(f"  ATR%:         {o['atr_pct']:>12.2f}%  (volatility)")

def make_orders_csv(orders):
    if not orders: return None
    df = pd.DataFrame(orders)
    cols = ["symbol","action","date","entry","stop","take","size","notional",
            "risk_usd","reward_usd","sl_pct","tp_pct","rr","rsi","atr_pct"]
    df = df[cols]
    p = LOGS/"test_orders.csv"
    df.to_csv(p, index=False)
    return p

def make_orders_html(orders):
    if not orders: return None
    rows = ""
    for o in orders:
        cls = "long" if o["action"] == "LONG" else "short"
        rows += f'''<tr class="{cls}">
          <td><b>{o['symbol']}</b></td>
          <td class="{cls}">{o['action']}</td>
          <td>${o['entry']:,.4f}</td>
          <td class="neg">${o['stop']:,.4f} <small>({o['sl_pct']:+.2f}%)</small></td>
          <td class="pos">${o['take']:,.4f} <small>({o['tp_pct']:+.2f}%)</small></td>
          <td>{o['size']:.6f}</td>
          <td>${o['notional']:,.2f}</td>
          <td class="neg">-${o['risk_usd']:.2f}</td>
          <td class="pos">+${o['reward_usd']:.2f}</td>
          <td><b>{o['rr']:.2f}</b></td>
          <td>{o['rsi']:.1f}</td>
        </tr>'''

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Test Orders</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;margin:0;padding:20px}}
h1{{color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px}}
.time{{color:#8b949e;font-size:13px;margin-bottom:15px}}
table{{width:100%;border-collapse:collapse;margin:15px 0;font-size:14px}}
th,td{{padding:12px 14px;text-align:right;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-size:11px;text-transform:uppercase;background:#161b22;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
tr:hover{{background:#161b22}}
.pos{{color:#3fb950}}.neg{{color:#f85149}}
tr.long{{border-left:3px solid #3fb950}}
tr.short{{border-left:3px solid #f85149}}
td.long{{color:#3fb950;font-weight:600}}
td.short{{color:#f85149;font-weight:600}}
small{{color:#8b949e;font-size:11px;font-weight:400}}
.info{{background:#161b22;border-left:3px solid #58a6ff;padding:15px;border-radius:6px;margin:15px 0}}
.info b{{color:#58a6ff}}
</style></head><body>
<h1>Test Orders - Live</h1>
<div class="time">Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
<div class="info">
<b>Capital:</b> ${CAPITAL:,.2f} &nbsp;|&nbsp;
<b>Risk per trade:</b> {RISK_PCT}% = ${CAPITAL * RISK_PCT / 100:,.2f} &nbsp;|&nbsp;
<b>R:R target:</b> 2.5 (TP=5xATR, SL=2xATR)
</div>
<table>
<tr>
  <th>Symbol</th><th>Action</th><th>Entry</th><th>Stop Loss</th><th>Take Profit</th>
  <th>Size</th><th>Notional</th><th>Risk $</th><th>Reward $</th><th>R:R</th><th>RSI</th>
</tr>
{rows}
</table>
</body></html>"""
    p = DASH/"test_orders.html"
    p.write_text(html)
    return p

def main():
    panel("TEST ORDERS - LIVE")
    print(f"  Capital: ${CAPITAL:,.2f}")
    print(f"  Risk per trade: {RISK_PCT}% (${CAPITAL*RISK_PCT/100:,.2f})")
    print(f"  R:R target: 2.5 (SL = 2xATR, TP = 5xATR)")

    panel("ORDER GENERATION")
    orders = []
    for sym in SYMBOLS:
        order = build_order(sym)
        if order:
            orders.append(order)
            render_order(order)
        else:
            print(f"\n  [SKIP] {sym} - no signal (RSI out of range)")

    if not orders:
        print("\n  No orders today. Wait for next signal.")
        return

    panel("SUMMARY")
    print(f"  Total orders: {len(orders)}")
    total_risk = sum(o["risk_usd"] for o in orders)
    total_reward = sum(o["reward_usd"] for o in orders)
    avg_rr = np.mean([o["rr"] for o in orders])
    print(f"  Total risk if all stops hit:    -${total_risk:.2f}")
    print(f"  Total reward if all takes hit:  +${total_reward:.2f}")
    print(f"  Portfolio R:R:                  {total_reward/total_risk:.2f}")
    print(f"  Avg R:R per trade:              {avg_rr:.2f}")
    print(f"  Risk of capital:                {total_risk/CAPITAL*100:.2f}%")

    panel("SAVING")
    csv = make_orders_csv(orders)
    print(f"  CSV:  {csv}")
    html = make_orders_html(orders)
    print(f"  HTML: {html}")

    panel("NEXT STEPS")
    print(f"  1. Проверить ордера в браузере")
    print(f"  2. Если ок - запустить в paper trade (без реальных $)")
    print(f"  3. Через 30 дней - заменить на API Bybit/Binance")
    print("="*W)

    webbrowser.open(f"file://{html.resolve()}")

if __name__ == "__main__":
    main()
