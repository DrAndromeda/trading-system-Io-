from pathlib import Path
p = Path("live_collector.py")
src = p.read_text()

# 1. В compute_mtf_signal добавляем entry/stop/take в каждый chart
old = '"charts": {tf: make_chart(dfs[tf], 80) for tf in TIMEFRAMES},'
new = '''"charts": {tf: {**make_chart(dfs[tf], 80),
                        "entry": round(entry, 6),
                        "stop": round(stop, 6),
                        "take": round(take, 6)} for tf in TIMEFRAMES},'''
if old not in src:
    print("❌ charts anchor not found"); raise SystemExit(1)
src = src.replace(old, new)

# 2. В HTML JS блоке renderChart добавляем линии entry/stop/take
old_js = '''  currentChart = new Chart(ctx, {{
    type: 'line',
    data: {{ labels: data.times, datasets: [
      {{ label: currentSym.replace('USDT','') + ' Price', data: data.close, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)', fill: true, tension: 0.1, pointRadius: 0, borderWidth: 2 }},
      {{ label: 'EMA20', data: data.ema20, borderColor: '#3fb950', borderWidth: 1.5, pointRadius: 0 }},
      {{ label: 'EMA50', data: data.ema50, borderColor: '#d29922', borderWidth: 1.5, pointRadius: 0 }}
    ]}},'''

new_js = '''  const n = data.times.length;
  const constLine = (val, color, label, dash) => ({{
    label: label, data: new Array(n).fill(val),
    borderColor: color, borderWidth: 2, borderDash: dash || [],
    pointRadius: 0, fill: false, tension: 0
  }});
  const entryPt = {{
    label: 'Entry', data: data.close.map((v,i) => i === n-1 ? data.entry : null),
    borderColor: '#fff', backgroundColor: '#fff',
    pointRadius: 8, pointStyle: 'triangle', showLine: false
  }};
  currentChart = new Chart(ctx, {{
    type: 'line',
    data: {{ labels: data.times, datasets: [
      {{ label: currentSym.replace('USDT','') + ' Price', data: data.close, borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)', fill: true, tension: 0.1, pointRadius: 0, borderWidth: 2, order: 10 }},
      {{ label: 'EMA20', data: data.ema20, borderColor: '#3fb950', borderWidth: 1.5, pointRadius: 0, order: 9 }},
      {{ label: 'EMA50', data: data.ema50, borderColor: '#d29922', borderWidth: 1.5, pointRadius: 0, order: 9 }},
      constLine(data.entry, '#ffffff', 'Entry $' + data.entry, [6,4]),
      constLine(data.take, '#3fb950', 'TP $' + data.take, [4,4]),
      constLine(data.stop, '#f85149', 'SL $' + data.stop, [4,4]),
      entryPt
    ]}},'''

if old_js not in src:
    print("❌ JS anchor not found"); raise SystemExit(1)
src = src.replace(old_js, new_js)

p.write_text(src)
print("✅ chart patch applied")
