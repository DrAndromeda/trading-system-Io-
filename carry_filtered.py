import itertools
import numpy as np
import pandas as pd

COST_SIDE = 0.0014   # вход или выход: спот 0.1% + перп 0.04%
CAP = 1.5            # капитал = номинал * (1 + 1/LEV), LEV = 2

def daily(sym):
    f = pd.read_csv(f"data/{sym}_funding.csv")
    f["time"] = pd.to_datetime(f["time"], utc=True, format="mixed")
    return f.set_index("time")["rate"].resample("1D").sum()

def run(d, n, enter, exit_):
    sig = (d.rolling(n).mean().shift(1) * 365).to_numpy()  # годовая, без look-ahead
    r = d.to_numpy()
    pos, rets, poss = 0, [], []
    for i in range(len(r)):
        s = sig[i]
        new = pos
        if np.isnan(s):
            new = 0
        elif pos == 0 and s > enter:
            new = 1
        elif pos == 1 and s < exit_:
            new = 0
        cost = COST_SIDE if new != pos else 0.0
        rets.append((r[i] * new - cost) / CAP)
        poss.append(new)
        pos = new
    return pd.Series(rets, d.index), pd.Series(poss, d.index)

GRID = list(itertools.product([7, 14, 30], [0.05, 0.10, 0.15], [0.0, 0.03]))

for sym in ["BTCUSDT", "ETHUSDT"]:
    d = daily(sym)
    runs = {g: run(d, *g) for g in GRID}
    print(f"\n{sym}  (параметры выбираются только по прошлым годам)")
    print(f"{'год':>5}{'always-on':>11}{'filtered':>10}{'в позиции':>11}   окно/вход/выход")
    A, F = [], []
    for y in range(2022, int(d.index.year.max()) + 1):
        train = {g: v[0][v[0].index.year < y].sum() for g, v in runs.items()}
        g = max(train, key=train.get)
        ret, pos = runs[g]
        m = ret.index.year == y
        filt = ret[m].sum() * 100
        alw = (d[d.index.year == y].sum() - 2 * COST_SIDE) / CAP * 100
        A.append(alw); F.append(filt)
        print(f"{y:>5}{alw:>10.2f}%{filt:>9.2f}%{pos[m].mean()*100:>10.0f}%   {g[0]}д/{g[1]*100:.0f}%/{g[2]*100:.0f}%")
    print(f"Среднее: always-on {np.mean(A):.2f}%  filtered {np.mean(F):.2f}%")
