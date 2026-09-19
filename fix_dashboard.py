from pathlib import Path
p = Path("live_collector.py")
src = p.read_text()

start = src.find("def update_dashboard(state, latest):")
end = src.find("def one_cycle(ex, state):")
if start == -1 or end == -1:
    print("❌ anchors not found"); raise SystemExit(1)

new_func = '''def update_dashboard(state, latest):
    positions = state.get("positions", {})
    closed = state.get("closed", [])

    # === TOTAL PORTFOLIO PNL ===
    total_pnl_usd = 0
    total_size = 0
    total_entry_value = 0
    for sym, p_data in positions.items():
        cur = next((s for s in latest if s["symbol"]==sym), None)
        if cur:
            pnl_usd = p_data["size"] * (cur["price"] - p_data["entry"])
            total_pnl_usd += pnl_usd
            total_size += p_data["size"] * cur["price"]
            total_entry_value += p_data["size"] * p_data["entry"]
    total_pnl_pct = (total_pnl_usd / total_entry_value * 100) if total_entry_value > 0 else 0

    closed_pnl = sum(c.get("pnl_pct",0) * 0.01 * c.get("size",0) * c.get("entry",0) for c in closed)
    all_time_pnl = total_pnl_usd + closed_pnl

    # === SIGNAL CARDS ===
    cards = ""
    for s in latest:
        if s["signal"] == "LONG":
            badge = '<div class="sig-badge long">🟢 LONG</div>'
            card_cls = "sig-long"
        elif s["signal"] == "WEAK_LONG":
            badge = '<div class="sig-badge weak">🟡 WAIT</div>'
            card_cls = "sig-weak"
        elif s["signal"] == "OVERBOUGHT":
            badge = '<div class="sig-badge short">🔴 OVERBOUGHT</div>'
            card_cls = "sig-short"
        elif s["signal"] == "OVERSOLD":
            badge = '<div class="sig-badge short">🔴 OVERSOLD</div>'
            card_cls = "sig-short"
        else:
            badge = '<div class="sig-badge wait">⚪ WAIT</div>'
            card_cls = "sig-wait"

        # Strength
        if s["confidence"] >= 80:
            strength = "STRONG"
            strength_color = "#3fb950"
        elif s["confidence"] >= 70:
            strength = "MEDIUM"
            strength_color = "#d29922"
        else:
            strength = "WEAK"
            strength_color = "#8b949e"

        size_usd = s["size"] * s["entry"]
        risk_usd = size_usd * (s["entry"] - s["stop"]) / s["entry"]
        reward_usd = size_usd * (s["take"] - s["entry"]) / s["entry"]

        # Trend summary
        trends = [s["trend_15m"], s["trend_1h"], s["trend_4h"], s["trend_1d"]]
        trend_up_count = sum(1 for t in trends if t == "up")
        trend_icon = "🟢🟢🟢🟢" if trend_up_count==4 else "🟢🟢🟢🔴" if trend_up_count==3 else "🟢🟢🔴🔴" if trend_up_count==2 else "🔴🔴🔴🔴"

        cards += f\'\'\'<div class="sig-card {card_cls}">
          <div class="sig-header">
            <div class="sig-symbol">{s["symbol"].replace("USDT","")}</div>
            {badge}
          </div>
          <div class="sig-price">${s["entry"]:,}</div>
          <div class="sig-strength">
            <span style="color:{strength_color};font-weight:700">{strength}</span>
            <span style="color:{strength_color}">{s["confidence"]}%</span>
          </div>
          <div class="strength-bar"><div class="strength-fill" style="width:{s["confidence"]}%;background:{strength_color}"></div></div>
          <div class="sig-info">
            <div class="sig-row"><span>RSI</span><span>{s["rsi_15m"]} / {s["rsi_1h"]} / {s["rsi_4h"]} / {s["rsi_1d"]}</span></div>
            <div class="sig-row"><span>Тренд</span><span>{trend_icon}</span></div>
            <div class="sig-row"><span>Size</span><span>${size_usd:,.0f}</span></div>
            <div class="sig-row"><span>Риск</span><span class="neg">-${risk_usd:,.0f}</span></div>
            <div class="sig-row"><span>Профит</span><span class="pos">+${reward_usd:,.0f}</span></div>
          </div>
        </div>\'\'\'

    # === POSITIONS ===
    pos_rows = ""
    for sym, p_data in positions.items():
        cur = next((s for s in latest if s["symbol"]==sym), None)
        if not cur: continue
        pnl_pct = (cur["price"]/p_data["entry"]-1)*100
        pnl_usd = p_data["size"] * (cur["price"] - p_data["entry"])
        cls = "pos" if pnl_pct>0 else "neg" if pnl_pct<0 else ""
        pnl_icon = "🟢" if pnl_pct > 0.1 else "🔴" if pnl_pct < -0.1 else "⚪"
        entry_str = f"${p_data[\'entry\']:,}"
        cur_str = f"${cur[\'price\']:,}"
        stop_str = f"${p_data[\'stop\']:,}"
        take_str = f"${p_data[\'take\']:,}"

        # Bar showing progress from stop to entry to take
        total_range = p_data["take"] - p_data["stop"]
        progress = ((cur["price"] - p_data["stop"]) / total_range * 100) if total_range > 0 else 50

        pos_rows += f\'\'\'<tr>
          <td><b>{sym.replace("USDT","")}</b></td>
          <td>{entry_str}</td>
          <td class="{cls}"><b>{cur_str}</b></td>
          <td class="{cls}"><b>{pnl_icon} {pnl_pct:+.2f}%</b></td>
          <td class="{cls}"><b>${pnl_usd:+,.2f}</b></td>
          <td class="neg">{stop_str}</td>
          <td class="pos">{take_str}</td>
          <td>
            <div class="progress-bar">
              <div class="progress-stop"></div>
              <div class="progress-now" style="left:{progress}%"></div>
              <div class="progress-take"></div>
            </div>
          </td>
          <td>{p_data.get(\'trail_updates\',0)}</td>
        </tr>\'\'\'

    if not pos_rows:
        pos_rows = \'<tr><td colspan="9" style="text-align:center;color:#8b949e;padding:30px">нет открытых позиций</td></tr>\'

    # === CHART DATA ===
    chart_json = json.dumps({s["symbol"]: s["chart"] for s in latest})

    # === PNL COLOR ===
    pnl_cls = "pos" if total_pnl_usd > 0 else "neg" if total_pnl_usd < 0 else ""
    pnl_icon = "🟢" if total_pnl_usd > 0 else "🔴" if total_pnl_usd < 0 else "⚪"

    now_ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>LIVE Trading Dashboard</title><meta http-equiv="refresh" content="30">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,system-ui,sans-serif;background:#0a0e14;color:#c9d1d9;padding:20px;font-size:14px}}
h1{{color:#58a6ff;margin-bottom:6px;font-size:22px}}
h2{{color:#58a6ff;margin:28px 0 12px;font-size:17px}}
.time{{color:#8b949e;font-size:12px;margin-bottom:20px}}

/* TOTAL PNL HEADER */
.total-header{{background:linear-gradient(135deg,#161b22 0%,#1c2128 100%);border:2px solid #30363d;border-radius:12px;padding:24px;margin-bottom:24px;text-align:center}}
.total-label{{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:1px}}
.total-value{{font-size:48px;font-weight:800;margin:8px 0;font-family:monospace}}
.total-value.pos{{color:#3fb950;text-shadow:0 0 20px rgba(63,185,80,0.3)}}
.total-value.neg{{color:#f85149;text-shadow:0 0 20px rgba(248,81,73,0.3)}}
.total-sub{{color:#8b949e;font-size:14px}}

/* TOP STATS */
.top-stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}}
.stat{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;text-align:center}}
.stat .lbl{{color:#8b949e;font-size:11px;text-transform:uppercase}}
.stat .val{{font-size:22px;font-weight:700;margin-top:6px}}

/* SIGNAL CARDS */
.sig-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:16px 0}}
.sig-card{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:16px;transition:transform 0.15s}}
.sig-card:hover{{transform:translateY(-2px)}}
.sig-long{{border-left:4px solid #3fb950;background:linear-gradient(135deg,#161b22 0%,rgba(63,185,80,0.08) 100%)}}
.sig-weak{{border-left:4px solid #d29922}}
.sig-short{{border-left:4px solid #f85149}}
.sig-wait{{border-left:4px solid #30363d;opacity:0.7}}
.sig-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.sig-symbol{{font-size:20px;font-weight:800;color:#fff}}
.sig-badge{{padding:4px 10px;border-radius:6px;font-size:11px;font-weight:700;letter-spacing:0.5px}}
.sig-badge.long{{background:rgba(63,185,80,0.25);color:#3fb950;border:1px solid #3fb950}}
.sig-badge.short{{background:rgba(248,81,73,0.25);color:#f85149;border:1px solid #f85149}}
.sig-badge.weak{{background:rgba(210,153,34,0.25);color:#d29922;border:1px solid #d29922}}
.sig-badge.wait{{background:rgba(139,148,158,0.15);color:#8b949e;border:1px solid #30363d}}
.sig-price{{font-size:26px;font-weight:800;color:#fff;font-family:monospace;margin-bottom:10px}}
.sig-strength{{display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px}}
.strength-bar{{height:6px;background:#21262d;border-radius:3px;overflow:hidden;margin-bottom:14px}}
.strength-fill{{height:100%;border-radius:3px;transition:width 0.3s}}
.sig-info{{font-size:12px}}
.sig-row{{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #21262d}}
.sig-row:last-child{{border-bottom:none}}
.sig-row span:first-child{{color:#8b949e}}

/* POSITIONS TABLE */
table{{width:100%;border-collapse:collapse;margin:12px 0;background:#161b22;border-radius:10px;overflow:hidden}}
th{{color:#8b949e;font-size:11px;text-transform:uppercase;background:#1c2128;padding:12px 10px;text-align:right;font-weight:600}}
td{{padding:14px 10px;text-align:right;border-bottom:1px solid #21262d;font-size:13px}}
th:first-child,td:first-child{{text-align:left}}
tr:last-child td{{border-bottom:none}}
.pos{{color:#3fb950;font-weight:700}}
.neg{{color:#f85149;font-weight:700}}

/* PROGRESS BAR */
.progress-bar{{position:relative;height:6px;background:#21262d;border-radius:3px;width:80px;display:inline-block}}
.progress-stop{{position:absolute;left:0;top:0;width:3px;height:6px;background:#f85149;border-radius:2px}}
.progress-take{{position:absolute;right:0;top:0;width:3px;height:6px;background:#3fb950;border-radius:2px}}
.progress-now{{position:absolute;top:-3px;width:2px;height:12px;background:#58a6ff;border-radius:1px;box-shadow:0 0 6px #58a6ff}}

/* CHARTS */
.chart-tabs{{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}}
.chart-tab{{padding:8px 16px;background:#21262d;border:1px solid #30363d;border-radius:6px;cursor:pointer;font-size:13px;color:#c9d1d9;font-weight:600}}
.chart-tab:hover{{background:#30363d}}
.chart-tab.active{{background:#1f6feb;color:#fff;border-color:#58a6ff}}
.chart-box{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;height:420px}}
canvas{{max-height:380px !important;height:380px !important}}

.info{{background:#161b22;border-left:3px solid #58a6ff;padding:12px 16px;border-radius:6px;margin:16px 0;font-size:12px;line-height:1.6;color:#8b949e}}
</style></head><body>

<h1>🎯 LIVE Trading Dashboard v5</h1>
<div class="time">Обновлено: {now_ts} | авто-обновление каждые 30 сек | цикл #{state[\'cycle\']}</div>

<!-- TOTAL PNL -->
<div class="total-header">
  <div class="total-label">💼 ОБЩАЯ ПРИБЫЛЬ ПОРТФЕЛЯ (открытые позиции)</div>
  <div class="total-value {pnl_cls}">{pnl_icon} ${total_pnl_usd:+,.2f}</div>
  <div class="total-sub">{total_pnl_pct:+.2f}% от вложенных ${total_entry_value:,.0f} | {len(positions)} позиции</div>
</div>

<!-- TOP STATS -->
<div class="top-stats">
  <div class="stat"><div class="lbl">Открыто</div><div class="val">{len(positions)}/{MAX_POSITIONS}</div></div>
  <div class="stat"><div class="lbl">Всего сигналов</div><div class="val pos">{len(state[\'signals\'])}</div></div>
  <div class="stat"><div class="lbl">Закрыто</div><div class="val">{len(closed)}</div></div>
  <div class="stat"><div class="lbl">Всего вложено</div><div class="val">${total_size:,.0f}</div></div>
  <div class="stat"><div class="lbl">Git push</div><div class="val">{state.get(\'pushes\',0)}</div></div>
</div>

<h2>📊 Сигналы по монетам</h2>
<div class="info">
<b>Как читать:</b> 🟢 LONG = покупаем сейчас | 🟡 WAIT = ждём подтверждения | 🔴 OVERBOUGHT/OVERSOLD = не входим.
<b>STRONG</b> = confidence 80%+ | <b>MEDIUM</b> = 70-79% | <b>WEAK</b> = ниже 70%.
</div>
<div class="sig-grid">
{cards}
</div>

<h2>💼 Открытые позиции</h2>
<table>
<tr>
  <th>Монета</th><th>Вход</th><th>Сейчас</th><th>PnL %</th><th>PnL $</th>
  <th>Stop</th><th>Take</th><th>Прогресс</th><th>Trails</th>
</tr>
{pos_rows}
</table>

<h2>📈 Графики (клик = открыть)</h2>
<div class="chart-tabs" id="tabs"></div>
<div class="chart-box">
  <canvas id="mainChart"></canvas>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
const CHARTS = {chart_json};
let currentChart = null;
function renderChart(sym) {{
  const data = CHARTS[sym];
  if (!data) return;
  document.querySelectorAll('.chart-tab').forEach(t => t.classList.toggle('active', t.dataset.sym === sym));
  const ctx = document.getElementById('mainChart').getContext('2d');
  if (currentChart) currentChart.destroy();
  currentChart = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: data.times,
      datasets: [
        {{ label: sym.replace('USDT','') + ' Price', data: data.close, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)', fill: true, tension: 0.1, pointRadius: 0, borderWidth: 2 }},
        {{ label: 'EMA20', data: data.ema20, borderColor: '#3fb950', borderWidth: 1.5, pointRadius: 0, tension: 0.1 }},
        {{ label: 'EMA50', data: data.ema50, borderColor: '#d29922', borderWidth: 1.5, pointRadius: 0, tension: 0.1 }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false, animation: {{ duration: 0 }},
      plugins: {{ legend: {{ labels: {{ color: '#c9d1d9', font: {{ size: 12 }} }} }}, title: {{ display: true, text: sym + ' — 15 минут', color: '#58a6ff', font: {{ size: 14 }} }} }},
      scales: {{ x: {{ ticks: {{ maxTicksLimit: 8, color: '#8b949e', font: {{ size: 10 }} }}, grid: {{ color: '#21262d' }} }}, y: {{ ticks: {{ color: '#8b949e', font: {{ size: 10 }} }}, grid: {{ color: '#21262d' }} }} }}
    }}
  }});
}}
const tabs = document.getElementById('tabs');
Object.keys(CHARTS).forEach((sym, i) => {{
  const t = document.createElement('div');
  t.className = 'chart-tab' + (i === 0 ? ' active' : '');
  t.dataset.sym = sym;
  t.textContent = sym.replace('USDT','');
  t.onclick = () => renderChart(sym);
  tabs.appendChild(t);
}});
if (Object.keys(CHARTS).length > 0) renderChart(Object.keys(CHARTS)[0]);
</script>
</body></html>"""

    (DASH/"live_stats.html").write_text(html)

'''

src = src[:start] + new_func + src[end:]
p.write_text(src)
print("✅ dashboard v5 установлен")
