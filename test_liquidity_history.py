#!/usr/bin/env python3
"""Бэктест стратегии ликвидности — правильная логика.
Пулы находятся один раз на всей истории, потом проверяются свипы."""
import numpy as np, requests
from liquidity_engine import find_equal_levels, detect_sweeps, detect_displacement

def fetch(symbol, interval="1h", limit=2000):
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={"symbol": symbol, "interval": interval, "limit": limit},
                     timeout=30)
    r.raise_for_status()
    k = r.json()
    return (
        np.array([float(x[1]) for x in k]),
        np.array([float(x[2]) for x in k]),
        np.array([float(x[3]) for x in k]),
        np.array([float(x[4]) for x in k]),
    )

def simple_atr_series(h, l, c, n=14):
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h-l, np.abs(h-pc), np.abs(l-pc)])
    atr = np.zeros_like(c)
    for i in range(len(c)):
        atr[i] = np.mean(tr[max(0, i-n+1):i+1])
    return atr

def simulate_liquidity(symbol, fee=0.0016, atr_mult=1.5, tp_r=2.0, lookback=20):
    o, h, l, c = fetch(symbol, "1h", 2000)
    n_bars = len(c)
    atr_arr = simple_atr_series(h, l, c, 14)

    # 1. Находим ВСЕ пулы на полной истории
    pools = find_equal_levels(h, l, lookback=lookback)
    print(f"  {symbol}: пулов найдено {len(pools)}")

    if not pools:
        return 0, 0, 0, 0

    # 2. Ищем все свипы на полной истории
    sweeps = detect_sweeps(o, h, l, c, pools, max_age=n_bars)
    print(f"  {symbol}: свипов найдено {len(sweeps)}")

    # 3. Проходим по свечам, ищем сигналы
    wins = losses = 0
    total_r = 0
    cooldown_until = 0

    # Сортируем свипы по индексу
    sweeps_sorted = sorted(sweeps, key=lambda s: s.sweep_index)

    for sweep in sweeps_sorted:
        i_sweep = sweep.sweep_index

        # Пропускаем если в кулдауне
        if i_sweep < cooldown_until:
            continue
        # Слишком близко к концу
        if i_sweep >= n_bars - 60:
            continue
        # Слишком рано
        if i_sweep < 50:
            continue

        # Проверяем displacement (в следующие 3 свечи)
        atr_val = atr_arr[i_sweep]
        if atr_val <= 0:
            continue

        has_disp = detect_displacement(o, h, l, c, sweep, atr_val, min_body_atr=1.2)
        if not has_disp:
            continue

        # Вход на свече после displacement (или на следующей)
        entry_i = min(i_sweep + 2, n_bars - 50)
        entry = c[entry_i]
        side = "LONG" if sweep.direction == "bullish" else "SHORT"
        R = atr_mult * atr_arr[entry_i]
        if R <= 0:
            continue

        if side == "LONG":
            stop = entry - R
            tp = entry + tp_r * R
        else:
            stop = entry + R
            tp = entry - tp_r * R

        # Симулируем выход (до 50 свечей)
        exit_price = None
        for j in range(entry_i + 1, min(entry_i + 51, n_bars)):
            if side == "LONG":
                if l[j] <= stop: exit_price = stop; break
                if h[j] >= tp: exit_price = tp; break
            else:
                if h[j] >= stop: exit_price = stop; break
                if l[j] <= tp: exit_price = tp; break

        if exit_price is None:
            exit_price = c[min(entry_i + 50, n_bars - 1)]

        if side == "LONG":
            raw_r = (exit_price - entry) / R
        else:
            raw_r = (entry - exit_price) / R

        cost = fee * entry / R
        net_r = raw_r - cost
        total_r += net_r
        if net_r > 0: wins += 1
        else: losses += 1

        # Кулдаун 10 свечей между сделками
        cooldown_until = entry_i + 10

    n = wins + losses
    wr = wins / n * 100 if n > 0 else 0
    exp = total_r / n if n > 0 else 0
    return n, wr, total_r, exp


if __name__ == "__main__":
    print("=" * 80)
    print("  БЭКТЕСТ LIQUIDITY STRATEGY — 2000 свечей 1h (~83 дня)")
    print("=" * 80)
    print(f"{'Актив':<10}{'Сделок':>10}{'WR':>10}{'R':>12}{'Expectancy':>14}")
    print("-" * 80)

    total_n = total_w = 0
    total_r = 0

    for sym in ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]:
        n, wr, r, exp = simulate_liquidity(sym)
        total_n += n
        total_w += int(n * wr / 100) if n > 0 else 0
        total_r += r
        print(f"{sym:<10}{n:>10}{wr:>9.1f}%{r:>+11.2f}{exp:>+13.3f}")

    print("-" * 80)
    if total_n > 0:
        wr_total = total_w / total_n * 100
        exp_total = total_r / total_n
        print(f"{'ИТОГО':<10}{total_n:>10}{wr_total:>9.1f}%{total_r:>+11.2f}{exp_total:>+13.3f}")

    print()
    print("Порог: expectancy > +0.3R = преимущество")
