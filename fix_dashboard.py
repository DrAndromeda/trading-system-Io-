import json, webbrowser
from pathlib import Path

DASH = Path("dashboard")
state = json.loads((DASH/"state.json").read_text())

# Встраиваем JSON прямо в HTML — никакого fetch
html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Crypto Carry Dashboard</title>
<style>
body{font-family:-apple-system,system-ui,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:20px}
h1{color:#58a6ff;border-bottom:2px solid #30363d;padding-bottom:10px}
h2{color:#58a6ff;margin-top:30px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:15px;margin:20px 0}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px}
.card .label{color:#8b949e;font-size:12px;text-transform:uppercase}
.card .value{font-size:26px;font-weight:600;margin-top:8px}
.pos{color:#3fb950}.neg{color:#f85149}
table{width:100%;border-collapse:collapse;margin:10px 0}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #21262d}
th{color:#8b949e;font-size:12px;text-transform:uppercase}
tr:hover{background:#161b22}
canvas{max-height:320px}
</style></head>
<body>
<h1>📊 Crypto Carry — Live Dashboard</h1>
<p style="color:#8b949e">Generated: <span id="ts"></span></p>
<div class="grid" id="cards"></div>
<h2>📈 Equity Curve</h2><canvas id="equity"></canvas>
<h2>📉 Drawdown</h2><canvas id="dd"></canvas>
<h2>🪙 Individual Symbols</h2><table id="indiv"></table>
<h2>📅 Per Year</h2><table id="yearly"></table>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
const D = __STATE__;
document.getElementById('ts').textContent = D.generated;
const p = D.portfolio;
document.getElementById('cards').innerHTML = `
  <div class="card"><div class="label">CAGR</div><div class="value ${p.cagr>0?'pos':'neg'}">${p.cagr.toFixed(2)}%</div></div>
  <div class="card"><div class="label">Sharpe</div><div class="value">${p.sharpe.toFixed(2)}</div></div>
  <div class="card"><div class="label">Max DD</div><div class="value neg">${p.dd.toFixed(2)}%</div></div>
  <div class="card"><div class="label">Total Return</div><div class="value ${p.total>0?'pos':'neg'}">${p.total.toFixed(1)}%</div></div>
  <div class="card"><div class="label">Days</div><div class="value">${p.n}</div></div>
  <div class="card"><div class="label">Vol</div><div class="value">${p.vol.toFixed(1)}%</div></div>`;
new Chart(document.getElementById('equity'), {type:'line', data:{labels:D.dates, datasets:[{label:'Equity', data:D.equity, borderColor:'#58a6ff', backgroundColor:'rgba(88,166,255,0.1)', fill:true, tension:0.2, pointRadius:0}]}, options:{responsive:true, plugins:{legend:{display:false}}, scales:{x:{ticks:{maxTicksLimit:10, color:'#8b949e'}, grid:{color:'#21262d'}}, y:{ticks:{color:'#8b949e'}, grid:{color:'#21262d'}}}}});
new Chart(document.getElementById('dd'), {type:'line', data:{labels:D.dates, datasets:[{label:'DD', data:D.drawdown, borderColor:'#f85149', backgroundColor:'rgba(248,81,73,0.1)', fill:true, tension:0.2, pointRadius:0}]}, options:{responsive:true, plugins:{legend:{display:false}}, scales:{x:{ticks:{maxTicksLimit:10, color:'#8b949e'}, grid:{color:'#21262d'}}, y:{ticks:{color:'#8b949e'}, grid:{color:'#21262d'}}}}});
let h = '<tr><th>Symbol</th><th>CAGR</th><th>Sharpe</th><th>DD</th></tr>';
for (const s of D.individual) h += `<tr><td><b>${s.symbol}</b></td><td class="${s.cagr>0?'pos':'neg'}">${s.cagr>0?'+':''}${s.cagr}%</td><td>${s.sharpe}</td><td class="neg">${s.dd}%</td></tr>`;
document.getElementById('indiv').innerHTML = h;
h = '<tr><th>Year</th><th>Return</th><th>Sharpe</th></tr>';
for (const y of D.yearly) h += `<tr><td><b>${y.year}</b></td><td class="${y.ret>0?'pos':'neg'}">${y.ret>0?'+':''}${y.ret}%</td><td>${y.sharpe}</td></tr>`;
document.getElementById('yearly').innerHTML = h;
</script>
</body></html>"""

html = html.replace("__STATE__", json.dumps(state))
(DASH/"index.html").write_text(html)
print(f"Fixed: {DASH/'index.html'}")
webbrowser.open(f"file://{(DASH/'index.html').resolve()}")
