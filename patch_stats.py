from pathlib import Path
p = Path("live_collector.py")
src = p.read_text()

# 1. Добавляем расчёт дневной и общей прибыли в update_dashboard
old = '''def update_dashboard(state, latest):
    positions = state.get("positions", {})
    closed = state.get("closed", [])
    CAP_PER_POS = CAPITAL / MAX_POSITIONS'''

new = '''def update_dashboard(state, latest):
    positions = state.get("positions", {})
    closed = state.get("closed", [])
    CAP_PER_POS = CAPITAL / MAX_POSITIONS

    # === РАСЧЁТ ПРИБЫЛИ В $ ===
    # Реализованная прибыль (все закрытые сделки)
    total_realized_usd = 0
    today_realized_usd = 0
    today_date = datetime.now(timezone.utc).date()
    for c in closed:
        size = c.get("size", 0)
        entry = c.get("entry", 0)
        exit_price = c.get("exit_price", 0)
        usd = size * (exit_price - entry) if size and entry else 0
        total_realized_usd += usd
        # Проверяем — сегодня ли закрыта
        exit_ts = c.get("exit_ts", "")
        if exit_ts:
            try:
                exit_date = datetime.fromisoformat(exit_ts.replace("Z","+00:00")).date()
                if exit_date == today_date:
                    today_realized_usd += usd
            except Exception:
                pass

    # Нереализованная (открытые позиции)
    unrealized_usd = 0
    for sym, p_data in positions.items():
        cur = next((s for s in latest if s["symbol"]==sym), None)
        if cur:
            unrealized_usd += p_data["size"] * (cur["price"] - p_data["entry"])

    # Депозит сейчас = начальный + реализованная + нереализованная
    current_deposit = CAPITAL + total_realized_usd + unrealized_usd'''

if old not in src:
    print("❌ anchor update_dashboard not found"); raise SystemExit(1)
src = src.replace(old, new)

# 2. Добавляем в HTML блок "Депозит + прибыль день/всё время"
old2 = '''<div class="total-header">
  <div class="total-label">💼 ПРИБЫЛЬ ПОРТФЕЛЯ СЕЙЧАС</div>
  <div class="total-value {pnl_cls}">${total_pnl_usd:+,.2f}</div>
  <div class="total-sub">{len(positions)}/{MAX_POSITIONS} позиций | общее плечо {total_lev:.1f}x</div>
</div>'''

new2 = '''<div class="total-header">
  <div class="total-label">💰 ДЕПОЗИТ СЕЙЧАС</div>
  <div class="total-value {dep_cls}">${current_deposit:,.2f}</div>
  <div class="total-sub">Начальный ${CAPITAL:,.0f} | {len(positions)}/{MAX_POSITIONS} позиций | плечо {total_lev:.1f}x</div>
</div>

<div class="profit-grid">
  <div class="profit-card">
    <div class="pl">📅 ПРИБЫЛЬ ЗА СЕГОДНЯ</div>
    <div class="pv {'pos' if today_realized_usd > 0 else 'neg' if today_realized_usd < 0 else ''}">${today_realized_usd:+,.2f}</div>
  </div>
  <div class="profit-card">
    <div class="pl">📊 ВСЕГО ЗАРАБОТАНО (реализ.)</div>
    <div class="pv {'pos' if total_realized_usd > 0 else 'neg' if total_realized_usd < 0 else ''}">${total_realized_usd:+,.2f}</div>
  </div>
  <div class="profit-card">
    <div class="pl">💼 В ОТКРЫТЫХ (нереализ.)</div>
    <div class="pv {'pos' if unrealized_usd > 0 else 'neg' if unrealized_usd < 0 else ''}">${unrealized_usd:+,.2f}</div>
  </div>
  <div class="profit-card">
    <div class="pl">🎯 СДЕЛОК ЗАКРЫТО</div>
    <div class="pv">{len(closed)}</div>
  </div>
</div>'''

if old2 not in src:
    print("❌ header anchor not found"); raise SystemExit(1)
src = src.replace(old2, new2)

# 3. Добавляем dep_cls переменную
old3 = '''    pnl_cls = "pos" if total_pnl_usd > 0 else "neg" if total_pnl_usd < 0 else ""
    now_ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")'''
new3 = '''    pnl_cls = "pos" if total_pnl_usd > 0 else "neg" if total_pnl_usd < 0 else ""
    dep_cls = "pos" if current_deposit > CAPITAL else "neg" if current_deposit < CAPITAL else ""
    now_ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")'''
if old3 in src:
    src = src.replace(old3, new3)

# 4. Добавляем CSS для profit-grid
old4 = '''.total-sub{{color:#8b949e;font-size:14px}}'''
new4 = '''.total-sub{{color:#8b949e;font-size:14px}}
.profit-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px}}
.profit-card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;text-align:center}}
.profit-card .pl{{color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:0.5px}}
.profit-card .pv{{font-size:22px;font-weight:700;margin-top:8px;font-family:monospace}}'''
if old4 in src:
    src = src.replace(old4, new4)
    print("✅ CSS added")
else:
    print("⚠️ CSS anchor not found (skip)")

# 5. В таблице открытых позиций — добавить Size $
old5 = '''<tr><th>Монета</th><th>Тип</th><th>Вход</th><th>Сейчас</th><th>PnL %</th><th>PnL $</th><th>Плечо</th><th>Stop</th><th>Take</th><th>Прогресс</th></tr>'''
new5 = '''<tr><th>Монета</th><th>Тип</th><th>Вход</th><th>Сейчас</th><th>Size $</th><th>PnL %</th><th>PnL $</th><th>Плечо</th><th>Stop</th><th>Take</th><th>Прогресс</th></tr>'''
if old5 in src:
    src = src.replace(old5, new5)
    print("✅ Table header updated")
else:
    print("⚠️ Table header not found")

# 6. В row открытой позиции — добавить Size $
old6 = '''          <td><b>{sym.replace("USDT","")}</b></td>
          <td><span class="badge-long">LONG</span></td>
          <td>${p_data["entry"]:,}</td>
          <td class="{cls}"><b>${cur["price"]:,}</b></td>
          <td class="{cls}"><b>{pnl_pct:+.2f}%</b></td>'''
new6 = '''          <td><b>{sym.replace("USDT","")}</b></td>
          <td><span class="badge-long">LONG</span></td>
          <td>${p_data["entry"]:,}</td>
          <td class="{cls}"><b>${cur["price"]:,}</b></td>
          <td><b>${p_data["size"] * cur["price"]:,.0f}</b></td>
          <td class="{cls}"><b>{pnl_pct:+.2f}%</b></td>'''
if old6 in src:
    src = src.replace(old6, new6)
    print("✅ Row updated with Size $")
else:
    print("⚠️ Row anchor not found")

# 7. В историю закрытых — добавить PnL $ колонку
old7 = '''<tr><th>Монета</th><th>Вход</th><th>Выход</th><th>PnL %</th><th>Причина</th><th>Дата входа</th></tr>'''
new7 = '''<tr><th>Монета</th><th>Вход</th><th>Выход</th><th>PnL %</th><th>PnL $</th><th>Причина</th><th>Дата входа</th></tr>'''
if old7 in src:
    src = src.replace(old7, new7)
    print("✅ History header updated")

# 8. В row истории — добавить PnL $
old8 = '''          <td><b>{c["symbol"].replace("USDT","")}</b></td>
          <td>${c["entry"]:,}</td>
          <td>${c.get("exit_price", 0):,}</td>
          <td class="{cls}"><b>{c.get("pnl_pct", 0):+.2f}%</b></td>
          <td class="{'pos' if reason=='TAKE' else 'neg'}">{icon}</td>'''
new8 = '''          <td><b>{c["symbol"].replace("USDT","")}</b></td>
          <td>${c["entry"]:,}</td>
          <td>${c.get("exit_price", 0):,}</td>
          <td class="{cls}"><b>{c.get("pnl_pct", 0):+.2f}%</b></td>
          <td class="{cls}"><b>${c.get("size",0) * (c.get("exit_price",0) - c.get("entry",0)):+,.2f}</b></td>
          <td class="{'pos' if reason=='TAKE' else 'neg'}">{icon}</td>'''
if old8 in src:
    src = src.replace(old8, new8)
    print("✅ History row updated with PnL $")

p.write_text(src)
print("✅ ALL PATCHES APPLIED")
