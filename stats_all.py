"""Собирает статистику из всех источников."""
import json
from pathlib import Path
from datetime import datetime

ROOT = Path("/Users/andromeda/crypto-carry")
LOGS = ROOT / "logs"

print("=" * 70)
print("  ПОЛНАЯ СТАТИСТИКА СИСТЕМЫ")
print("=" * 70)

# 1. LIVE HISTORY — все проверки
live_file = LOGS / "live.jsonl"
if live_file.exists():
    live = [json.loads(l) for l in live_file.read_text().splitlines() if l.strip()]
    print(f"\n📊 LIVE (все проверки):")
    print(f"  Всего записей: {len(live)}")
    by_sym = {}
    for s in live:
        by_sym[s["symbol"]] = by_sym.get(s["symbol"], 0) + 1
    for sym, cnt in sorted(by_sym.items(), key=lambda x: -x[1]):
        print(f"    {sym}: {cnt}")

# 2. CONFIRMED — сигналы
sig_file = LOGS / "signals_confirmed.jsonl"
if sig_file.exists():
    sigs = [json.loads(l) for l in sig_file.read_text().splitlines() if l.strip()]
    print(f"\n🎯 CONFIRMED SIGNALS: {len(sigs)}")
    for s in sigs[-10:]:
        print(f"  {s['ts'][:16]} {s['symbol']:10} conf={s['confidence']}% RSI={s.get('rsi',{}).get('15m',0) if isinstance(s.get('rsi'),dict) else 0}")

# 3. REAL ORDERS — реальные ордера
ro_file = LOGS / "real_orders.jsonl"
if ro_file.exists():
    orders = [json.loads(l) for l in ro_file.read_text().splitlines() if l.strip()]
    print(f"\n💰 REAL ORDERS: {len(orders)}")
    for o in orders:
        print(f"  {o['ts'][:16]} {o['symbol']:10} entry=${o['entry']} qty={o['qty']} notional=${o['notional']}")

# 4. REAL PLACED — все позиции (открытые+закрытые)
rp_file = LOGS / "real_placed.json"
if rp_file.exists():
    placed = json.loads(rp_file.read_text())
    open_pos = [v for v in placed.values() if not v.get("closed")]
    closed_pos = [v for v in placed.values() if v.get("closed")]
    print(f"\n📦 REAL PLACED:")
    print(f"  Открыто: {len(open_pos)}")
    for p in open_pos:
        print(f"    {p['symbol']:10} entry=${p.get('entry',0)}")
    print(f"  Закрыто: {len(closed_pos)}")
    for p in closed_pos:
        print(f"    {p['symbol']:10} closed @ {p.get('closed_ts','?')[:16]}")

# 5. LIVE STATE — paper сделки
ls_file = ROOT / "live_state.json"
if ls_file.exists():
    state = json.loads(ls_file.read_text())
    closed = state.get("closed", [])
    print(f"\n📈 PAPER STATE:")
    print(f"  Cycle: {state.get('cycle',0)}")
    print(f"  Signals: {len(state.get('signals',[]))}")
    print(f"  Open: {len(state.get('positions',{}))}")
    print(f"  Closed: {len(closed)}")
    if closed:
        wins = [c for c in closed if c.get("pnl_pct",0) > 0]
        print(f"  Win Rate: {len(wins)}/{len(closed)} = {len(wins)/len(closed)*100:.0f}%")
        print(f"\n  Последние закрытые:")
        for c in closed[-10:]:
            print(f"    {c['symbol']:10} {c.get('exit_reason','?'):6} {c.get('pnl_pct',0):+.2f}%")

print()
print("=" * 70)
print("  ИСТОЧНИКИ ДАННЫХ:")
print(f"  {LOGS}/live.jsonl              — все LONG сигналы")
print(f"  {LOGS}/signals_confirmed.jsonl — подтверждённые")
print(f"  {LOGS}/real_orders.jsonl       — реальные ордера")
print(f"  {LOGS}/real_placed.json        — открытые/закрытые")
print(f"  {ROOT}/live_state.json         — paper сделки")
print("=" * 70)
