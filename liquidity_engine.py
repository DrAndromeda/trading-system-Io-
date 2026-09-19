#!/usr/bin/env python3
"""
Liquidity Engine — детектор пулов ликвидности и свипов.
Работает с OHLCV данными, находит:
- Equal Highs / Equal Lows (пулы стопов)
- Liquidity Sweeps (сбор стопов)
- Смещение после свипа (подтверждение)
Возвращает сигналы для интеграции в trader.py.
"""
import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class LiquidityPool:
    price: float
    type: str          # "BSL" или "SSL"
    index: int         # индекс свечи
    strength: float    # насколько "равные" уровни (1.0 = идеально равные)


@dataclass
class Sweep:
    pool: LiquidityPool
    sweep_index: int
    sweep_high: float   # для BSL — максимальный high
    sweep_low: float    # для SSL — минимальный low
    close_back: float   # цена закрытия после свипа
    direction: str      # "bullish" (после SSL) или "bearish" (после BSL)


def find_equal_levels(highs: np.ndarray, lows: np.ndarray,
                     lookback: int = 20, tolerance: float = 0.0015) -> list[LiquidityPool]:
    """
    Находит пулы ликвидности (равные максимумы/минимумы).
    tolerance — максимальное относительное расхождение (0.15% по умолчанию).
    """
    pools = []
    n = len(highs)

    # Equal Highs (BSL) — стопы шортистов
    for i in range(lookback, n - lookback):
        h_i = highs[i]
        # Ищем все хаи в окне, "равные" текущему
        window = highs[i - lookback:i + lookback + 1]
        equal_count = int(np.sum(np.abs(window - h_i) / h_i < tolerance))
        # Если есть хотя бы 2 равных уровня и текущий — максимум окна
        if equal_count >= 2 and h_i == window.max():
            strength = min(1.0, equal_count / 4.0)
            pools.append(LiquidityPool(
                price=float(h_i), type="BSL", index=i, strength=strength
            ))

    # Equal Lows (SSL) — стопы лонгистов
    for i in range(lookback, n - lookback):
        l_i = lows[i]
        window = lows[i - lookback:i + lookback + 1]
        equal_count = int(np.sum(np.abs(window - l_i) / l_i < tolerance))
        if equal_count >= 2 and l_i == window.min():
            strength = min(1.0, equal_count / 4.0)
            pools.append(LiquidityPool(
                price=float(l_i), type="SSL", index=i, strength=strength
            ))

    return pools


def detect_sweeps(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                 closes: np.ndarray, pools: list[LiquidityPool],
                 max_age: int = 10) -> list[Sweep]:
    """
    Ищет свипы (пробой пула с возвратом).
    max_age — сколько свечей назад считать свип "свежим".
    """
    sweeps = []
    n = len(closes)

    for pool in pools:
        # Свип должен произойти ПОСЛЕ формирования пула
        start = max(pool.index + 1, n - max_age)
        if start >= n:
            continue

        for i in range(start, n):
            # BSL-свип: high пробил уровень, close вернулся ниже
            if pool.type == "BSL":
                if highs[i] > pool.price and closes[i] < pool.price:
                    sweeps.append(Sweep(
                        pool=pool, sweep_index=i,
                        sweep_high=float(highs[i]),
                        sweep_low=float(lows[i]),
                        close_back=float(closes[i]),
                        direction="bearish",
                    ))
                    break  # только первый свип этого пула

            # SSL-свип: low пробил уровень, close вернулся выше
            elif pool.type == "SSL":
                if lows[i] < pool.price and closes[i] > pool.price:
                    sweeps.append(Sweep(
                        pool=pool, sweep_index=i,
                        sweep_high=float(highs[i]),
                        sweep_low=float(lows[i]),
                        close_back=float(closes[i]),
                        direction="bullish",
                    ))
                    break

    return sweeps


def detect_displacement(opens: np.ndarray, highs: np.ndarray,
                       lows: np.ndarray, closes: np.ndarray,
                       sweep: Sweep, atr_value: float,
                       min_body_atr: float = 1.2) -> bool:
    """
    После свипа ищем смещение (Displacement) — импульсную свечу
    в противоположную свипу сторону. Это подтверждение.
    """
    start = sweep.sweep_index + 1
    end = min(start + 3, len(closes))

    for i in range(start, end):
        body = abs(closes[i] - opens[i])
        body_atr = body / atr_value if atr_value > 0 else 0

        if sweep.direction == "bullish" and closes[i] > opens[i]:
            # Зелёная свеча с телом > 1.2 ATR → displacement
            if body_atr >= min_body_atr:
                return True
        elif sweep.direction == "bearish" and closes[i] < opens[i]:
            if body_atr >= min_body_atr:
                return True

    return False


def analyze_liquidity(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                     closes: np.ndarray, atr_value: float,
                     lookback: int = 20) -> dict:
    """
    Полный анализ: пулы → свипы → подтверждение.
    Возвращает словарь с последним актуальным сигналом.
    """
    pools = find_equal_levels(highs, lows, lookback=lookback)
    sweeps = detect_sweeps(opens, highs, lows, closes, pools, max_age=10)

    result = {
        "pools_count": len(pools),
        "sweeps_count": len(sweeps),
        "last_sweep": None,
        "signal": None,
        "reason": "",
        "nearby_pools": [],
    }

    if not sweeps:
        result["reason"] = "свипов нет"
        # Всё равно покажем ближайшие пулы
        last_price = float(closes[-1])
        sorted_pools = sorted(pools, key=lambda p: abs(p.price - last_price))
        result["nearby_pools"] = [
            {"price": p.price, "type": p.type,
             "distance_pct": round((p.price - last_price) / last_price * 100, 2),
             "strength": round(p.strength, 2)}
            for p in sorted_pools[:5]
        ]
        return result

    # Берём самый свежий свип
    last_sweep = max(sweeps, key=lambda s: s.sweep_index)
    age = len(closes) - 1 - last_sweep.sweep_index

    # Свип должен быть свежим (< 5 свечей)
    if age > 5:
        result["reason"] = f"последний свип {age} свечей назад — устарел"
    else:
        # Проверяем displacement
        has_disp = detect_displacement(opens, highs, lows, closes, last_sweep, atr_value)

        result["last_sweep"] = {
            "pool_price": last_sweep.pool.price,
            "pool_type": last_sweep.pool.type,
            "pool_strength": round(last_sweep.pool.strength, 2),
            "sweep_index": last_sweep.sweep_index,
            "age": age,
            "direction": last_sweep.direction,
            "sweep_high": last_sweep.sweep_high,
            "sweep_low": last_sweep.sweep_low,
        }

        if has_disp:
            result["signal"] = "LONG" if last_sweep.direction == "bullish" else "SHORT"
            result["reason"] = (
                f"{last_sweep.pool.type}-свип ({age} свечей) + displacement"
            )
        else:
            result["reason"] = (
                f"{last_sweep.pool.type}-свип есть, но нет displacement (нет импульса)"
            )

    # Всегда показываем ближайшие пулы
    last_price = float(closes[-1])
    sorted_pools = sorted(pools, key=lambda p: abs(p.price - last_price))
    result["nearby_pools"] = [
        {"price": p.price, "type": p.type,
         "distance_pct": round((p.price - last_price) / last_price * 100, 2),
         "strength": round(p.strength, 2)}
        for p in sorted_pools[:5]
    ]

    return result


# === ТЕСТ ===
if __name__ == "__main__":
    import requests

    def fetch(symbol, interval="1h", limit=500):
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

    def simple_atr(h, l, c, n=14):
        pc = np.concatenate([[c[0]], c[:-1]])
        tr = np.maximum.reduce([h-l, np.abs(h-c), np.abs(l-pc)])
        return float(np.mean(tr[-n:]))

    print("=" * 75)
    print("  LIQUIDITY ENGINE — ТЕСТ НА BTC/ETH/XRP/SOL (1h, 500 свечей)")
    print("=" * 75)

    for sym in ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT"]:
        o, h, l, c = fetch(sym, "1h", 500)
        atr = simple_atr(h, l, c)
        r = analyze_liquidity(o, h, l, c, atr)

        print(f"\n{sym}")
        print(f"  Пулов: {r['pools_count']}  ·  Свипов: {r['sweeps_count']}")
        print(f"  Сигнал: {r['signal'] or '—'}  ·  {r['reason']}")

        if r.get("last_sweep"):
            s = r["last_sweep"]
            print(f"  Последний свип: {s['pool_type']} @ {s['pool_price']:.2f} "
                  f"({s['age']} свечей назад, direction={s['direction']})")

        if r["nearby_pools"]:
            print(f"  Ближайшие пулы:")
            for p in r["nearby_pools"][:3]:
                print(f"    {p['type']:<4} @ {p['price']:>10.2f}  "
                      f"{p['distance_pct']:+.2f}%  strength={p['strength']}")
