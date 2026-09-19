#!/usr/bin/env python3
"""
CARRY MONITOR v2 — единая система для funding + quarterly basis.
- Опрашивает Binance каждые 5 минут
- Считает net carry после комиссий
- Генерирует JSON + HTML панель
- Открывает в браузере автоматически
"""
import requests, time, json, threading, webbrowser
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = Path(__file__).resolve().parent
DASH = ROOT / "dashboard"; DASH.mkdir(exist_ok=True)
STATE = DASH / "state.json"
HTML = DASH / "index.html"

# === КОНФИГ ===
ASSETS = {
    "BTC": {"spot": "BTCUSDT", "fut": "BTCUSDT"},
    "ETH": {"spot": "ETHUSDT", "fut": "ETHUSDT"},
}
FEE_SPOT = 0.001        # 0.1% Binance taker
FEE_PERP = 0.0004       # 0.04%
SLIPPAGE = 0.0005       # 0.05% на ногу

# Пороги для сигнала
MIN_NET_ANN_PCT = 5.0   # минимум 5% годовых, чтобы открывать

# === ДАННЫЕ ===
def fetch_spot(symbol):
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={"symbol": symbol, "interval": "1d", "limit": 30},
                     timeout=20)
    r.raise_for_status()
    k = r.json()
    return float(k[-1][4])

def fetch_funding(symbol, limit=10):
    r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                     params={"symbol": symbol, "limit": limit},
                     timeout=20)
    r.raise_for_status()
    return [float(x["fundingRate"]) for x in r.json()]

def fetch_basis(symbol):
    r = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                     params={"symbol": symbol}, timeout=20)
    r.raise_for_status()
    d = r.json()
    mark = float(d["markPrice"])
    index = float(d["indexPrice"])
    basis_pct = (mark / index - 1) * 100
    return {
        "mark": mark, "index": index,
        "basis_pct": basis_pct,
        "ann_pct": basis_pct * 365 / 90,
    }

# === РАСЧЁТЫ ===
def calc_funding_net(rates, days=30):
    if not rates: return None
    avg = float(np.mean(rates))
    n_settle = days * 3
    gross_pct = avg * n_settle * 100
    # Издержки: вход + выход по обеим ногам
    costs_pct = (FEE_SPOT + FEE_PERP + SLIPPAGE * 2) * 2 * 100
    net_pct = gross_pct - costs_pct
    return {
        "avg_rate_pct": avg * 100,
        "gross_pct": round(gross_pct, 3),
        "costs_pct": round(costs_pct, 3),
        "net_pct": round(net_pct, 3),
        "net_ann_pct": round(net_pct / days * 365, 2),
    }

def calc_quarterly_net(basis, days=90):
    if not basis: return None
    gross_ann = basis["ann_pct"]
    costs_ann = ((FEE_SPOT + FEE_PERP) * 2 + SLIPPAGE * 2) * 100 * (365 / days)
    net_ann = gross_ann - costs_ann
    return {
        "gross_ann_pct": round(gross_ann, 2),
        "costs_ann_pct": round(costs_ann, 2),
        "net_ann_pct": round(net_ann, 2),
    }

# === СБОРКА СОСТОЯНИЯ ===
def build_state():
    assets_out = {}
    for name, sym in ASSETS.items():
        try:
            spot = fetch_spot(sym["spot"])
            rates = fetch_funding(sym["fut"])
            basis = fetch_basis(sym["fut"])
            f_net = calc_funding_net(rates)
            q_net = calc_quarterly_net(basis)

            # Решение
            best = "funding" if f_net["net_ann_pct"] > q_net["net_ann_pct"] else "quarterly"
            best_ann = max(f_net["net_ann_pct"], q_net["net_ann_pct"])
            signal = "OPEN" if best_ann >= MIN_NET_ANN_PCT else "WAIT"

            assets_out[name] = {
                "spot": round(spot, 2),
                "funding": f_net,
                "quarterly": q_net,
                "best_strategy": best,
                "best_net_ann_pct": round(best_ann, 2),
                "signal": signal,
            }
        except Exception as e:
            assets_out[name] = {"error": str(e)}

    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_net_ann_pct": MIN_NET_ANN_PCT,
        "assets": assets_out,
    }

# === РЕНДЕР ===
def render_html(state):
    return f'''<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><title>Carry Monitor</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--bg:#0a0e14;--card:#131820;--card2:#1a212b;--bd:#232c38;--tx:#e6edf3;--mu:#7d8896;
--gr:#2ecc71;--rd:#ff4d5e;--yl:#f5b400;--bl:#4a9eff}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:var(--bg);color:var(--tx);padding:24px;font-size:14px}}
header{{display:flex;justify-content:space-between;align-items:center;
margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--bd)}}
h1{{font-size:20px;font-weight:600}}
h1 span{{color:var(--mu);font-size:12px;display:block;margin-top:4px;font-weight:400}}
.meta{{color:var(--mu);font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:20px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:20px}}
.card.open{{border-color:var(--gr);box-shadow:0 0 0 2px rgba(46,204,113,.1)}}
.card.wait{{border-color:var(--bd)}}
.card-h{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}}
.asset{{font-size:18px;font-weight:600}}
.price{{text-align:right}}
.price .v{{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}}
.price .c{{font-size:11px;color:var(--mu)}}
.signal{{display:inline-block;padding:5px 14px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.05em}}
.signal.open{{background:rgba(46,204,113,.15);color:var(--gr)}}
.signal.wait{{background:rgba(125,136,150,.15);color:var(--mu)}}
.block{{background:var(--card2);border-radius:10px;padding:14px;margin-bottom:12px}}
.block-title{{font-size:11px;color:var(--mu);text-transform:uppercase;letter-spacing:.06em;
margin-bottom:10px;font-weight:600;display:flex;justify-content:space-between}}
.block-title .best{{color:var(--yl)}}
.row{{display:flex;justify-content:space-between;padding:6px 0;font-size:13px;
border-bottom:1px solid rgba(35,44,56,.5)}}
.row:last-child{{border:none}}
.row .lbl{{color:var(--mu)}}
.row .val{{font-variant-numeric:tabular-nums;font-weight:600}}
.pos{{color:var(--gr)}}.neg{{color:var(--rd)}}.warn{{color:var(--yl)}}
footer{{margin-top:24px;padding-top:16px;border-top:1px solid var(--bd);
color:var(--mu);font-size:11px;line-height:1.7}}
</style></head><body>
<header>
  <h1>Carry Monitor<span>funding + quarterly basis · net после комиссий</span></h1>
  <div class="meta">обновлено <span id="upd">—</span><br>poll каждые 30 с</div>
</header>
<div class="grid" id="grid"></div>
<footer>
  <b>Funding arbitrage:</b> лонг спот + шорт перп. Прибыль от funding, платится 3 раза в день.<br>
  <b>Quarterly basis:</b> лонг спот + шорт квартальный фьючерс. Прибыль от сходимости к экспирации.<br>
  <b>Издержки:</b> spot 0.1% + perp/quarterly 0.04% × 2 стороны + slippage 0.05% × 2.<br>
  <b>Порог:</b> сигнал OPEN если net carry ≥ {state["min_net_ann_pct"]}% годовых.<br>
  Это инструмент, не инвестиционная рекомендация. Нужен доступ к спотy и фьючерсам.
</footer>
<script>
const fmt = v => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
const cls = v => v == null ? "" : (v > 0 ? "pos" : v < 0 ? "neg" : "");
async function load() {{
  const r = await fetch("state.json?t=" + Date.now());
  const s = await r.json();
  document.getElementById("upd").textContent = s.updated.slice(0, 19).replace("T", " ");
  const g = document.getElementById("grid");
  g.innerHTML = "";
  for (const [name, a] of Object.entries(s.assets)) {{
    if (a.error) {{
      g.innerHTML += `<div class="card"><div class="asset">${{name}}</div><div style="color:var(--rd)">${{a.error}}</div></div>`;
      continue;
    }}
    const cls_ = a.signal === "OPEN" ? "open" : "wait";
    g.innerHTML += `
      <div class="card ${{cls_}}">
        <div class="card-h">
          <div class="asset">${{name}}/USDT</div>
          <div class="price">
            <div class="v">$${{a.spot.toLocaleString()}}</div>
            <div class="c">спот цена</div>
          </div>
        </div>
        <span class="signal ${{cls_}}">${{a.signal}}</span>
        <span style="margin-left:10px;color:var(--mu);font-size:12px">
          лучшая: ${{a.best_strategy}} (${{fmt(a.best_net_ann_pct)}} годовых)
        </span>

        <div class="block" style="margin-top:16px">
          <div class="block-title">
            <span>Funding arbitrage (30 дней)</span>
            <span class="${{a.funding.net_ann_pct > a.quarterly.net_ann_pct ? 'best' : ''}}">
              ${{a.funding.net_ann_pct > a.quarterly.net_ann_pct ? 'ВЫБРАНА' : ''}}
            </span>
          </div>
          <div class="row"><span class="lbl">Средний funding (8ч)</span>
            <span class="val">${{a.funding.avg_rate_pct.toFixed(4)}}%</span></div>
          <div class="row"><span class="lbl">Gross (30д)</span>
            <span class="val ${{cls(a.funding.gross_pct)}}">${{fmt(a.funding.gross_pct)}}</span></div>
          <div class="row"><span class="lbl">Издержки</span>
            <span class="val neg">−${{a.funding.costs_pct.toFixed(2)}}%</span></div>
          <div class="row"><span class="lbl"><b>Net (30д)</b></span>
            <span class="val ${{cls(a.funding.net_pct)}}"><b>${{fmt(a.funding.net_pct)}}</b></span></div>
          <div class="row"><span class="lbl"><b>Net годовых</b></span>
            <span class="val ${{cls(a.funding.net_ann_pct)}}"><b>${{fmt(a.funding.net_ann_pct)}}</b></span></div>
        </div>

        <div class="block">
          <div class="block-title">
            <span>Quarterly basis (90 дней)</span>
            <span class="${{a.quarterly.net_ann_pct > a.funding.net_ann_pct ? 'best' : ''}}">
              ${{a.quarterly.net_ann_pct > a.funding.net_ann_pct ? 'ВЫБРАНА' : ''}}
            </span>
          </div>
          <div class="row"><span class="lbl">Gross годовых</span>
            <span class="val ${{cls(a.quarterly.gross_ann_pct)}}">${{fmt(a.quarterly.gross_ann_pct)}}</span></div>
          <div class="row"><span class="lbl">Издержки годовых</span>
            <span class="val neg">−${{a.quarterly.costs_ann_pct.toFixed(2)}}%</span></div>
          <div class="row"><span class="lbl"><b>Net годовых</b></span>
            <span class="val ${{cls(a.quarterly.net_ann_pct)}}"><b>${{fmt(a.quarterly.net_ann_pct)}}</b></span></div>
        </div>
      </div>`;
  }}
}}
load();
setInterval(load, 30000);
</script></body></html>'''

def update():
    try:
        s = build_state()
        STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")
        HTML.write_text(render_html(s), encoding="utf-8")
        for name, a in s["assets"].items():
            if "error" in a:
                print(f"  {name}: ошибка {a['error']}")
            else:
                print(f"  {name}: {a['signal']} · best={a['best_strategy']} "
                      f"{a['best_net_ann_pct']:+.2f}% годовых · "
                      f"funding={a['funding']['net_ann_pct']:+.2f}% quarterly={a['quarterly']['net_ann_pct']:+.2f}%")
    except Exception as e:
        print(f"ошибка update: {e}")

def serve():
    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(DASH), **kw)
        def log_message(self, *a): pass
    HTTP = HTTPServer(("127.0.0.1", 8001), H)
    print("Сервер: http://127.0.0.1:8001/")
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8001/")).start()
    HTTP.serve_forever()

def loop(interval=300):
    while True:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] обновление...")
        update()
        time.sleep(interval)

if __name__ == "__main__":
    import sys
    print("=" * 70)
    print("  CARRY MONITOR v2")
    print("=" * 70)
    update()
    if "--serve" in sys.argv:
        threading.Thread(target=loop, daemon=True).start()
        serve()
    elif "--watch" in sys.argv:
        loop(300)
