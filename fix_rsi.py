from pathlib import Path
p = Path("trading_bot.py")
src = p.read_text()

# Заменяем trade_plan — добавляем RSI-фильтр
old = '''def trade_plan(sym, d, f, capital=10000.0, risk_pct=1.0, rr=2.0):
    price = d["price"]; atr_v = d["atr"]
    if atr_v <= 0: return None
    direction = "LONG" if d["trend"] == "UP" else "SHORT"
    stop_dist = atr_v * 2.0
    take_dist = stop_dist * rr
    if direction == "LONG":
        stop = price - stop_dist; take = price + take_dist
    else:
        stop = price + stop_dist; take = price - take_dist
    risk_usd = capital * risk_pct / 100
    size = risk_usd / stop_dist if stop_dist > 0 else 0

    action = direction
    if f and f["active"] and f["annual_avg"] > 8:
        action = "CARRY"
        stop_dist = atr_v * 3.0
        take_dist = atr_v * 1.0
        stop = price - stop_dist
        take = price + take_dist'''

new = '''def trade_plan(sym, d, f, capital=10000.0, risk_pct=1.0, rr=2.0):
    price = d["price"]; atr_v = d["atr"]
    rsi_v = d["rsi"]
    if atr_v <= 0: return None

    # RSI-фильтр: не входим в LONG при перекупленности
    if rsi_v > 75:
        action = "WAIT"
        stop_dist = atr_v * 2.0
        take_dist = atr_v * 2.0
        stop = price - stop_dist
        take = price + take_dist
    else:
        direction = "LONG" if d["trend"] == "UP" else "SHORT"
        stop_dist = atr_v * 2.0
        take_dist = stop_dist * rr
        if direction == "LONG":
            stop = price - stop_dist; take = price + take_dist
        else:
            stop = price + stop_dist; take = price - take_dist
        action = direction
        # CARRY только если funding высокий и RSI не экстремальный
        if f and f["active"] and f["annual_avg"] > 8 and rsi_v < 75:
            action = "CARRY"
            stop_dist = atr_v * 3.0
            take_dist = atr_v * 1.0
            stop = price - stop_dist
            take = price + take_dist

    risk_usd = capital * risk_pct / 100
    size = risk_usd / stop_dist if stop_dist > 0 else 0'''

if old in src:
    src = src.replace(old, new)
    p.write_text(src)
    print("OK: RSI-фильтр добавлен")
else:
    print("WARN: anchor не найден")
