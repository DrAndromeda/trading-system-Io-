import itertools, json
import numpy as np, pandas as pd
from pathlib import Path

COST_SIDE = 0.0014
CAP = 1.5
DOCS = Path("docs"); DOCS.mkdir(exist_ok=True)
W = 90

def panel(t): print("="*W); print("  "+t); print("="*W)

def daily(sym):
    f = pd.read_csv(f"data/{sym}_funding.csv")
    f["time"] = pd.to_datetime(f["time"], utc=True, format="mixed")
    bad = f["time"].isna().sum()
    if bad: print(f"  WARN {sym}: {bad} bad timestamps dropped")
    f = f.dropna(subset=["time"])
    return f.set_index("time")["rate"].resample("1D").sum()

def run(d, n, enter, exit_, allow_flip=True):
    sig = (d.rolling(n).mean().shift(1) * 365).to_numpy()
    r = d.to_numpy()
    pos, rets, poss = 0, [], []
    for i in range(len(r)):
        s = sig[i]
        new = pos
        if np.isnan(s):
            new = 0
        elif pos == 0:
            if s > enter: new = 1
            elif allow_flip and s < -enter: new = -1
        elif pos == 1 and s < exit_:
            new = 0
        elif pos == -1 and s > -exit_:
            new = 0
        cost = COST_SIDE if new != pos else 0.0
        # carry P&L: pos=1 → +funding, pos=-1 → -funding
        rets.append((r[i] * new - cost) / CAP)
        poss.append(new)
        pos = new
    return pd.Series(rets, d.index), pd.Series(poss, d.index)

def metrics(ret):
    if len(ret) == 0: return dict(total=0, sharpe=0, dd=0, calmar=0)
    cum = (1 + ret).cumprod()
    total = (cum.iloc[-1] - 1) * 100
    dd = ((cum / cum.cummax()) - 1).min() * 100
    sh = ret.mean() / ret.std() * np.sqrt(365) if ret.std() > 0 else 0
    years = len(ret) / 365
    cagr = ((cum.iloc[-1]) ** (1/years) - 1) * 100 if years > 0 else 0
    calmar = cagr / abs(dd) if dd != 0 else 0
    return dict(total=total, sharpe=sh, dd=dd, calmar=calmar)

GRID = list(itertools.product([7, 14, 30], [0.05, 0.10, 0.15], [0.0, 0.03]))

def walk_forward(d, grid, flip=True):
    runs = {g: run(d, *g, allow_flip=flip) for g in grid}
    rows = []
    for y in range(2022, int(d.index.year.max()) + 1):
        # select by train Sharpe (not total return)
        best_g, best_sh = None, -1e9
        for g, (ret, _) in runs.items():
            tr = ret[ret.index.year < y]
            if len(tr) < 60: continue
            sh = tr.mean() / tr.std() * np.sqrt(365) if tr.std() > 0 else 0
            if sh > best_sh:
                best_sh, best_g = sh, g
        if best_g is None: continue
        ret, pos = runs[best_g]
        m = ret.index.year == y
        yret = ret[m]
        ypos = pos[m]
        mm = metrics(yret)
        alw = (d[d.index.year == y].sum() - 2*COST_SIDE) / CAP * 100
        rows.append(dict(year=y, window=best_g[0], enter=best_g[1], exit_=best_g[2],
                         filtered=mm["total"], always=alw, exposure=ypos.abs().mean()*100,
                         sharpe=mm["sharpe"], dd=mm["dd"], calmar=mm["calmar"]))
    return pd.DataFrame(rows)

def write_docs(sym, df_off, df_on):
    p = DOCS / f"carry_{sym}.md"
    lines = []
    lines.append(f"# Carry Strategy — {sym}\n\n")
    lines.append("Auto-generated. Walk-forward by year.\n\n")
    lines.append("## Config\n\n")
    lines.append(f"- Cost per side: {COST_SIDE*100:.2f}%\n")
    lines.append(f"- Capital: notional × {CAP} (LEV=2)\n")
    lines.append("- Grid: window [7,14,30], enter [5,10,15]%, exit [0,3]%\n")
    lines.append("- Selection: best train Sharpe on all prior years\n\n")
    for name, df in [("flip OFF", df_off), ("flip ON", df_on)]:
        lines.append(f"## {name}\n\n")
        lines.append("| year | win | enter | exit | filtered% | always% | expo% | Sharpe | DD% | Calmar |\n")
        lines.append("|---|---|---|---|---|---|---|---|---|---|\n")
        for _, r in df.iterrows():
            lines.append(f"| {int(r['year'])} | {int(r['window'])} | {r['enter']*100:.0f}% | "
                         f"{r['exit_']*100:.0f}% | {r['filtered']:+.2f} | {r['always']:+.2f} | "
                         f"{r['exposure']:.0f} | {r['sharpe']:+.2f} | {r['dd']:+.2f} | {r['calmar']:+.2f} |\n")
        lines.append(f"\nAvg filtered: {df['filtered'].mean():+.2f}%  |  "
                     f"Avg always: {df['always'].mean():+.2f}%  |  "
                     f"Avg Sharpe: {df['sharpe'].mean():+.2f}  |  "
                     f"Avg DD: {df['dd'].mean():+.2f}%\n\n")
    p.write_text("".join(lines))
    return p

def main():
    panel("CARRY FILTERED v2 — WALK-FORWARD + RISK METRICS + FLIP")
    all_docs = []
    for sym in ["BTCUSDT", "ETHUSDT"]:
        try:
            d = daily(sym)
        except FileNotFoundError:
            print(f"  SKIP {sym}: data/{sym}_funding.csv not found")
            continue
        print(f"\n  {sym}: {len(d)} days ({d.index[0].date()} -> {d.index[-1].date()})")

        for flip in (False, True):
            panel(f"{sym} — flip={'ON' if flip else 'OFF'}")
            tbl = walk_forward(d, GRID, flip=flip)
            if tbl.empty: print("  no data"); continue
            print(f"  {'year':>5}{'win':>5}{'ent':>6}{'ex':>5}{'filt%':>9}{'alw%':>9}"
                  f"{'expo%':>7}{'Shp':>7}{'DD%':>7}{'Cal':>7}")
            print("  "+"-"*(W-2))
            for _, r in tbl.iterrows():
                print(f"  {int(r['year']):>5}{int(r['window']):>5}{r['enter']*100:>5.0f}%"
                      f"{r['exit_']*100:>4.0f}%{r['filtered']:>+8.2f}%{r['always']:>+8.2f}%"
                      f"{r['exposure']:>6.0f}%{r['sharpe']:>+7.2f}{r['dd']:>+7.2f}{r['calmar']:>+7.2f}")
            print(f"\n  AVG: filtered {tbl['filtered'].mean():+.2f}%  |  "
                  f"always {tbl['always'].mean():+.2f}%  |  "
                  f"Sharpe {tbl['sharpe'].mean():+.2f}  |  "
                  f"DD {tbl['dd'].mean():+.2f}%")

        off = walk_forward(d, GRID, flip=False)
        on = walk_forward(d, GRID, flip=True)
        p = write_docs(sym, off, on)
        all_docs.append(str(p))

    panel("DOCS WRITTEN")
    for p in all_docs:
        print(f"  {p}  ({Path(p).stat().st_size} bytes)")
    print("="*W)
    print("  DONE")
    print("="*W)

if __name__ == "__main__":
    main()
