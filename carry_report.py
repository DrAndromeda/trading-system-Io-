import pandas as pd

COST_RT = 0.0028   # вход+выход, обе ноги: спот 0.1% + перп 0.04% в каждую сторону
LEV = 2.0          # плечо на перпе; капитал = спот + маржа = N * (1 + 1/LEV)

for sym in ["BTCUSDT", "ETHUSDT"]:
    f = pd.read_csv(f"data/{sym}_funding.csv")
    f["time"] = pd.to_datetime(f["time"], utc=True, format="mixed")
    f["year"] = f["time"].dt.year
    print("\n" + sym)
    print(f"{'год':>5}{'периодов':>9}{'funding %':>11}{'после комис.':>14}{'на капитал':>12}{'доля <0':>9}")
    for y, g in f.groupby("year"):
        tot = g["rate"].sum() * 100
        net = tot - COST_RT * 100
        cap = net / (1 + 1 / LEV)
        neg = (g["rate"] < 0).mean() * 100
        print(f"{y:>5}{len(g):>9}{tot:>10.2f}%{net:>13.2f}%{cap:>11.2f}%{neg:>8.0f}%")
    worst30 = f["rate"].rolling(90).sum().min() * 100
    print(f"Худшие 30 дней подряд: {worst30:.2f}% от номинала")
