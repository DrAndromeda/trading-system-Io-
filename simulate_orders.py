"""
ORDER SIMULATOR - показываем как работает в реальной истории
=============================================================
Тот же entry/SL/TP логик, но прогнанный по всем барам.
Для каждого ордера показывает: entry date, exit date, reason, P&L.
"""
import json, warnings, webbrowser
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
DATA = Path("data"); DASH = Path("dashboard"); LOGS = Path("logs")
for p in [DASH, LOGS]: p.mkdir(exist_ok=True)
W = 110

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]
CAPITAL = 10000.0
RISK_PCT = 1.0
RR = 2.5
SL_ATR = 2.0

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

def simulate(sym):
    """Прогон ордеров по всей истории для символа."""
    df1h = load_1h(sym)
    if df1h is None: return None, None
    dfd = to_daily(df1h)
    d = dfd.copy()
    d["ema20"] = ema(d["close"],20); d["ema50"] = ema(d["close"],50)
    d["rsi"] = rsi(d["close"],14); d["atr"] = atr(d,14)
    d["atr_pct"] = d["atr"]/d["close"]*100

    trades = []
    position = None
    equity = CAPITAL
    eq_curve = [equity]

    dates = d.index.tolist()
    for i in range(1, len(d)):
        row = d.iloc[i]; prev = d.iloc[i-1]
        price_row = row

        if position is None:
            # Условие сигнала (то же что в build_order)
            if (prev["ema20"] > prev["ema50"]) and (50 < prev["rsi"] < 75):
                entry_price = row["open"]
                atr_v = prev["atr"]
                if pd.isna(atr_v) or atr_v <= 0:
                    eq_curve.append(equity); continue
                sl_dist = SL_ATR * atr_v
                tp_dist = SL_ATR * atr_v * RR
                stop = entry_price - sl_dist
                take = entry_price + tp_dist
                risk_usd = CAPITAL * RISK_PCT / 100
                size = risk_usd / sl_dist

                position = {
                    "symbol": sym, "side": "LONG",
                    "entry_date": row.name, "entry": entry_price,
                    "stop": stop, "take": take,
                    "atr": atr_v, "size": size, "risk_usd": risk_usd,
                    "bars": 1,
                }
            eq_curve.append(equity)
        else:
            # Проверяем SL/TP внутри бара
            hit, exit_price = None, None
            if row["low"] <= position["stop"]:
                hit = "STOP"; exit_price = position["stop"]
            elif row["high"] >= position["take"]:
                hit = "TAKE"; exit_price = position["take"]

            position["bars"] += 1

            if hit:
                pnl_pct = (exit_price / position["entry"] - 1) * 100
                pnl_usd = (exit_price - position["entry"]) * position["size"]
                pnl_usd -= (position["entry"] + exit_price) * position["size"] * 0.001
                equity += pnl_usd
                trades.append({
                    "symbol": sym, "side": position["side"],
                    "entry_date": position["entry_date"].date(),
                    "exit_date": row.name.date(),
                    "entry": round(position["entry"],4),
                    "stop": round(position["stop"],4),
                    "take": round(position["take"],4),
                    "exit": round(exit_price,4),
                    "reason": hit,
                    "pnl_pct": round(pnl_pct,2),
                    "pnl_usd": round(pnl_usd,2),
                    "bars_held": position["bars"],
                    "equity_after": round(equity,2),
                })
                position = None
            eq_curve.append(equity)

    return trades, eq_curve

def metrics(trades):
    if not trades: return {}
    df = pd.DataFrame(trades)
    wins = df[df["pnl_usd"] > 0]; losses = df[df["pnl_usd"] <= 0]
    wr = len(wins) / len(df) * 100
    pf = wins["pnl_usd"].sum() / abs(losses["pnl_usd"].sum()) if len(losses) and losses["pnl_usd"].sum() != 0 else 0
    return {
        "trades": len(df),
        "win_rate": round(wr, 1),
        "pnl_usd": round(df["pnl_usd"].sum(), 2),
        "avg_win": round(wins["pnl_usd"].mean(), 2) if len(wins) else 0,
        "avg_loss": round(losses["pnl_usd"].mean(), 2) if len(losses) else 0,
        "pf": round(pf, 2),
        "stops": int((df["reason"]=="STOP").sum()),
        "takes": int((df["reason"]=="TAKE").sum()),
        "avg_bars": round(df["bars_held"].mean(), 1),
        "best": round(df["pnl_usd"].max(), 2),
        "worst": round(df["pnl_usd"].min(), 2),
    }

def make_trade_chart(sym, dfd, trades):
    """График цены + метки входов/выходов."""
    if not trades: return None
    fig, ax = plt.subplots(figsize=(14, 6), facecolor="#0d1117")
    ax.set_facecolor("#161b22")
    ax.plot(dfd.index, dfd["close"], color="#58a6ff", linewidth=1, alpha=0.7)

    for t in trades:
        entry_x = pd.Timestamp(t["entry_date"])
        exit_x = pd.Timestamp(t["exit_date"])
        color = "#3fb950" if t["pnl_pct"] > 0 else "#f85149"
        marker = "^" if t["side"] == "LONG" else "v"
        ax.scatter([entry_x], [t["entry"]], color=color, s=80, marker=marker, zorder=5,
                   edgecolors="#c9d1d9", linewidths=0.5)
        ax.scatter([exit_x], [t["exit"]], color=color, s=40, marker="x", zorder=5)

    ax.set_title(f"{sym} - {len(trades)} trades | Total PnL ${sum(t['pnl_usd'] for t in trades):+,.0f}",
                 color="#c9d1d9", fontsize=13)
    ax.tick_params(colors="#8b949e")
    ax.grid(True, alpha=0.1, color="#8b949e")
    for spine in ax.spines.values(): spine.set_color("#30363d")
    plt.tight_layout()
    p = DASH/f"trades_{sym}.png"
    plt.savefig(p, dpi=100, facecolor="#0d1117")
    plt.close()
    return p

def make_orders_html(all_trades, results):
    rows = ""
    for t in all_trades:
        cls = "pos" if t["pnl_usd"] > 0 else "neg"
        reason_cls = "pos" if t["reason"] == "TAKE" else "neg"
        rows += f'''<tr>
          <td><b>{t['symbol']}</b></td>
          <td>{t['entry_date']}</td>
          <td>{t['exit_date']}</td>
          <td>{t['side']}</td>
          <td>${t['entry']:,.4f}</td>
          <td class="neg">${t['stop']:,.4f}</td>
          <td class="pos">${t['take']:,.4f}</td>
          <td>${t['exit']:,.4f}</td>
          <td class="{reason_cls}"><b>{t['reason']}</b></td>
          <td class="{cls}">{t['pnl_pct']:+.2f}%</td>
          <td class="{cls}">${t['pnl_usd']:+,.2f}</td>
          <td>{t['bars_held']}</td>
        </tr>'''

    summary_rows = ""
    for r in results:
        summary_rows += f'''<tr>
          <td><b>{r['symbol']}</b></td>
          <td>{r['trades']}</td>
          <td class="{'pos' if r['win_rate']>40 else 'neg'}">{r['win_rate']}%</td>
          <td class="{'pos' if r['pnl_usd']>0 else 'neg'}">${r['pnl_usd']:+,.2f}</td>
          <td class="pos">${r['avg_win']:+,.2f}</td>
          <td class="neg">${r['avg_loss']:+,.2f}</td>
          <td class="{'pos' if r['pf']>1.2 else 'neg'}">{r['pf']}</td>
          <td>{r['stops']}</td>
          <td>{r['takes']}</td>
          <td>{r['avg_bars']}</td>
        </tr>'''

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Order Simulator</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;margin:0;padding:20px}}
h1{{color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px}}
h2{{color:#58a6ff;margin-top:35px}}
.time{{color:#8b949e;font-size:13px;margin-bottom:15px}}
table{{width:100%;border-collapse:collapse;margin:15px 0;font-size:13px}}
th,td{{padding:8px 10px;text-align:right;border-bottom:1px solid #21262d}}
th{{color:#8b949e;font-size:10px;text-transform:uppercase;background:#161b22}}
th:first-child,td:first-child{{text-align:left}}
tr:hover{{background:#161b22}}
.pos{{color:#3fb950}}.neg{{color:#f85149}}
.info{{background:#161b22;border-left:3px solid #58a6ff;padding:15px;border-radius:6px;margin:15px 0}}
.info b{{color:#58a6ff}}
.chart{{background:#161b22;border-radius:8px;padding:10px;margin:15px 0;text-align:center}}
.chart img{{max-width:100%;border-radius:6px}}
</style></head><body>
<h1>Order Simulator - Real History</h1>
<div class="time">Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
<div class="info">
<b>Config:</b> Capital ${CAPITAL:,.0f} | Risk {RISK_PCT}% | R:R {RR} | SL={SL_ATR}xATR | TP={RR*SL_ATR}xATR<br>
<b>Logic:</b> LONG if EMA20>EMA50 AND RSI 50-75 | Entry next open | Intra-bar SL/TP
</div>
<h2>Per-Symbol Summary</h2>
<table>
<tr><th>Symbol</th><th>Trades</th><th>Win%</th><th>PnL $</th><th>Avg Win</th><th>Avg Loss</th><th>PF</th><th>Stops</th><th>Takes</th><th>Avg Bars</th></tr>
{summary_rows}
</table>
<h2>All Trades</h2>
<table>
<tr><th>Symbol</th><th>Entry Date</th><th>Exit Date</th><th>Side</th><th>Entry</th><th>Stop</th><th>Take</th><th>Exit</th><th>Reason</th><th>PnL%</th><th>PnL $</th><th>Bars</th></tr>
{rows}
</table>
</body></html>"""
    p = DASH/"simulator.html"
    p.write_text(html)
    return p

def main():
    panel("ORDER SIMULATOR - реальная история")
    print(f"  Config: Capital ${CAPITAL:,.0f} | Risk {RISK_PCT}% | R:R {RR}")
    print(f"  Logic: LONG if EMA20>EMA50 & RSI 50-75")

    all_trades = []
    results = []
    charts = []

    for sym in SYMBOLS:
        panel(sym)
        df1h = load_1h(sym)
        if df1h is None: continue
        dfd = to_daily(df1h)
        trades, eq = simulate(sym)
        if not trades:
            print("  no trades")
            continue
        m = metrics(trades)
        m["symbol"] = sym
        results.append(m)
        all_trades.extend(trades)

        print(f"  Trades: {m['trades']}  WR: {m['win_rate']}%  PnL: ${m['pnl_usd']:+,.2f}  PF: {m['pf']}")
        print(f"  Stops {m['stops']} / Takes {m['takes']}  Avg bars {m['avg_bars']}  Best ${m['best']:+,.2f}  Worst ${m['worst']:+,.2f}")

        # Last 3 trades
        print(f"\n  Последние 3 сделки:")
        for t in trades[-3:]:
            mark = "OK" if t["pnl_usd"] > 0 else "XX"
            print(f"    {mark} {t['entry_date']} -> {t['exit_date']}  "
                  f"entry ${t['entry']}  exit ${t['exit']}  "
                  f"{t['reason']}  {t['pnl_pct']:+.2f}%  (${t['pnl_usd']:+.2f})")

        chart = make_trade_chart(sym, dfd, trades)
        if chart: charts.append(chart)
        print()

    # Summary
    panel("SUMMARY")
    print(f"  {'sym':<10}{'trades':>7}{'win%':>7}{'PnL$':>10}{'PF':>6}{'stops':>7}{'takes':>7}")
    print("  " + "-"*(W-2))
    total_pnl = 0
    for r in results:
        total_pnl += r["pnl_usd"]
        print(f"  {r['symbol']:<10}{r['trades']:>7}{r['win_rate']:>6.1f}%"
              f"{r['pnl_usd']:>+9.2f}${r['pf']:>6.2f}{r['stops']:>7}{r['takes']:>7}")
    print(f"\n  TOTAL PnL across all symbols: ${total_pnl:+,.2f}")
    print(f"  Total trades: {len(all_trades)}")

    # Save CSV
    if all_trades:
        df = pd.DataFrame(all_trades)
        csv_path = LOGS/"simulated_orders.csv"
        df.to_csv(csv_path, index=False)
        print(f"\n  CSV: {csv_path}")

    # HTML
    html = make_orders_html(all_trades, results)
    print(f"  HTML: {html}")

    panel("DONE")
    print(f"  Charts: {len(charts)}")
    print(f"  Trades: {len(all_trades)}")
    print(f"  Total: ${total_pnl:+,.2f} on ${CAPITAL:,.0f} capital ({total_pnl/CAPITAL*100:+.1f}%)")
    print("="*W)

    webbrowser.open(f"file://{html.resolve()}")

if __name__ == "__main__":
    main()
