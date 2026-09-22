from pathlib import Path
p = Path("/Users/andromeda/crypto-carry/live_collector.py")
src = p.read_text()

start = src.find("def update_dashboard(state, latest):")
if start == -1:
    print("❌ update_dashboard not found"); raise SystemExit(1)
next_def = src.find("\ndef ", start + 5)
if next_def == -1:
    print("❌ end of function not found"); raise SystemExit(1)

# Найдём существующий блок HTML и заменим только графики
# Ищем от "const CHARTS =" до "</script>" в старом HTML
new_chart_block = '''
const chartsData = __CHARTS_JSON__;
const chartContainer = document.getElementById('mainChart').parentElement;
chartContainer.innerHTML = '<div id="lwcChart" style="width:100%;height:400px;"></div>';

function initChart(symbol) {
    const data = chartsData[symbol];
    if (!data) return;

    // Удаляем старый
    const existing = document.getElementById('lwcChart');
    if (existing) existing.remove();
    const newDiv = document.createElement('div');
    newDiv.id = 'lwcChart';
    newDiv.style.width = '100%';
    newDiv.style.height = '400px';
    chartContainer.appendChild(newDiv);

    const chart = LightweightCharts.createChart(newDiv, {
        width: newDiv.clientWidth,
        height: 400,
        layout: { background: { color: '#0a0e14' }, textColor: '#c9d1d9' },
        grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
        timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
        rightPriceScale: { borderColor: '#30363d' },
    });

    const series = chart.addCandlestickSeries({
        upColor: '#3fb950', downColor: '#f85149',
        borderUpColor: '#3fb950', borderDownColor: '#f85149',
        wickUpColor: '#3fb950', wickDownColor: '#f85149',
    });
    series.setData(data.candles);

    if (data.entry) {
        series.setMarkers([{
            time: data.candles[data.candles.length-1].time,
            position: 'belowBar', color: '#fff',
            shape: 'arrowUp', text: 'ENTRY'
        }]);
    }
    if (data.stop) {
        series.createPriceLine({ price: data.stop, color: '#f85149',
            lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: 'STOP' });
    }
    if (data.take) {
        series.createPriceLine({ price: data.take, color: '#3fb950',
            lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: 'TAKE' });
    }
    chart.timeScale().fitContent();
    window.addEventListener('resize', () => chart.resize(newDiv.clientWidth, 400));
}

// Переключение монет
window.renderSymbol = function(sym) {
    document.querySelectorAll('.chart-tab').forEach(t => t.classList.toggle('active', t.dataset.sym === sym));
    initChart(sym);
};
if (Object.keys(chartsData).length > 0) {
    const first = Object.keys(chartsData)[0];
    setTimeout(() => window.renderSymbol(first), 100);
}
'''

# Заменяем старый chart блок
old_start = src.find("const CHARTS = ", start)
old_end = src.find("</script>", old_start)
if old_start == -1 or old_end == -1:
    print("❌ chart JS block not found"); raise SystemExit(1)

# Найдём закрывающий </script> и вставим новый блок
src = src[:old_start] + new_chart_block + "\n" + src[old_end:]

# Добавляем <script> Lightweight Charts в head
src = src.replace(
    '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>',
    '<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>'
)

p.write_text(src)
print("✅ Lightweight Charts установлен")
print("   (старый Chart.js удалён, новый LWC добавлен)")
