#!/usr/bin/env python3
"""
ML на on-chain данных.
- Фичи: returns, hashrate, active addresses, MA crossovers, volatility
- Модель: GradientBoostingClassifier (sklearn)
- Walk-forward: train на 60 дней, test на 30, сдвиг на 30
- Метрики: AUC, accuracy, Sharpe стратегии, vs baseline
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score, accuracy_score
import warnings
warnings.filterwarnings("ignore")

DATA = Path("data/onchain")

def load_data():
    d = json.loads((DATA / "blockchain_info.json").read_text())
    df = pd.DataFrame(d["price"]).rename(columns={"value": "price", "ts": "ts"})
    for key in ["hashrate", "active_addresses", "tx_count", "tx_volume_usd", "miners_revenue"]:
        if key in d:
            tmp = pd.DataFrame(d[key]).rename(columns={"value": key})
            df = pd.merge(df, tmp, on="ts", how="outer")
    df = df.sort_values("ts").reset_index(drop=True).ffill().dropna().reset_index(drop=True)
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def build_features(df):
    """Фичи для модели. Все — лагированные, без look-ahead."""
    out = pd.DataFrame()
    out["price"] = df["price"]
    if "date" in df.columns:
        out["date"] = df["date"]

    # Returns разных горизонтов
    for n in [1, 3, 7, 14, 30]:
        out[f"ret_{n}d"] = df["price"].pct_change(n)

    # Hashrate: изменение, z-score
    if "hashrate" in df:
        out["hr_chg_7d"] = df["hashrate"].pct_change(7)
        out["hr_chg_30d"] = df["hashrate"].pct_change(30)
        out["hr_ma_ratio"] = df["hashrate"] / df["hashrate"].rolling(30).mean()

    # Active addresses
    if "active_addresses" in df:
        out["aa_chg_7d"] = df["active_addresses"].pct_change(7)
        out["aa_chg_30d"] = df["active_addresses"].pct_change(30)
        out["aa_ma_ratio"] = df["active_addresses"] / df["active_addresses"].rolling(30).mean()

    # Volume
    if "tx_volume_usd" in df:
        out["vol_chg_7d"] = df["tx_volume_usd"].pct_change(7)
        out["vol_ma_ratio"] = df["tx_volume_usd"] / df["tx_volume_usd"].rolling(30).mean()

    # Miners revenue
    if "miners_revenue" in df:
        out["mr_ma_ratio"] = df["miners_revenue"] / df["miners_revenue"].rolling(30).mean()

    # Volatility
    out["vol_30d"] = df["price"].pct_change().rolling(30).std()

    # MA crossovers
    out["price_ma_7_30"] = df["price"].rolling(7).mean() / df["price"].rolling(30).mean()
    out["price_vs_ma_30"] = df["price"] / df["price"].rolling(30).mean()

    # Target: цена выше через 3 дня?
    out["target"] = (df["price"].shift(-3) > df["price"]).astype(int)

    return out

def walk_forward(df, train_days=60, test_days=30, horizon=3):
    """Walk-forward с переобучением на каждом окне."""
    feat_cols = [c for c in df.columns if c not in ["price", "target", "date"]]
    n = len(df)
    results = []

    start = train_days
    while start + test_days <= n:
        train = df.iloc[start - train_days:start].dropna()
        test = df.iloc[start:start + test_days].dropna()

        if len(train) < 30 or len(test) < 10:
            start += test_days
            continue

        X_train = train[feat_cols].values
        y_train = train["target"].values
        X_test = test[feat_cols].values
        y_test = test["target"].values

        if len(np.unique(y_train)) < 2:
            start += test_days
            continue

        # Модель
        model = GradientBoostingClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.05,
            random_state=42,
        )
        model.fit(X_train, y_train)

        # Прогноз
        y_pred_proba = model.predict_proba(X_test)[:, 1]
        y_pred = (y_pred_proba > 0.5).astype(int)

        try:
            auc = roc_auc_score(y_test, y_pred_proba)
        except Exception:
            auc = 0.5
        acc = accuracy_score(y_test, y_pred)

        # Sharpe стратегии (long если predict=1, flat иначе)
        test_ret = test["price"].pct_change().shift(-1).fillna(0).values
        pos = y_pred
        eq = pos * test_ret - np.abs(np.diff(np.concatenate([[0], pos]))) * 0.0016
        if len(eq) > 2 and np.std(eq) > 0:
            sharpe = np.mean(eq) / np.std(eq) * np.sqrt(365)
        else:
            sharpe = 0

        # Buy & hold на этом окне
        bh = (test["price"].iloc[-1] / test["price"].iloc[0] - 1) * 100

        results.append({
            "start": test["date"].iloc[0].date(),
            "end": test["date"].iloc[-1].date(),
            "auc": auc, "acc": acc,
            "n_test": len(test),
            "long_frac": pos.mean(),
            "sharpe": sharpe,
            "test_ret_pct": np.sum(eq) * 100,
            "bh_pct": bh,
        })
        start += test_days

    return results, model, feat_cols

if __name__ == "__main__":
    print("=" * 95)
    print("  ML ON-CHAIN — GRADIENT BOOSTING, WALK-FORWARD")
    print("=" * 95)

    df_raw = load_data()
    print(f"Данных: {len(df_raw)} дней ({df_raw['date'].iloc[0].date()} → {df_raw['date'].iloc[-1].date()})")

    df = build_features(df_raw)
    print(f"Фич: {len([c for c in df.columns if c not in ['price', 'target']])}")

    results, model, feat_cols = walk_forward(df, train_days=60, test_days=30, horizon=3)

    print(f"\nWalk-forward: {len(results)} окон\n")
    print(f"{'Окно':<25}{'AUC':>7}{'Acc':>7}{'Long%':>8}{'Sharpe':>8}{'Test%':>9}{'BH%':>9}{'Alpha':>9}")
    print("-" * 95)

    total_alpha = 0
    for r in results:
        alpha = r["test_ret_pct"] - r["bh_pct"]
        total_alpha += alpha
        print(f"{str(r['start']):<12} → {str(r['end']):<9}"
              f"{r['auc']:>6.2f}{r['acc']:>7.2f}{r['long_frac']:>7.0%}"
              f"{r['sharpe']:>8.2f}{r['test_ret_pct']:>+8.1f}%{r['bh_pct']:>+8.1f}%"
              f"{alpha:>+8.1f}%")

    # Сводка
    print("-" * 95)
    avg_auc = np.mean([r["auc"] for r in results])
    avg_acc = np.mean([r["acc"] for r in results])
    avg_sharpe = np.mean([r["sharpe"] for r in results])
    wins = sum(1 for r in results if r["test_ret_pct"] > r["bh_pct"])

    print(f"\nСредний AUC:    {avg_auc:.3f}   (0.5 = монетка, >0.55 = есть сигнал)")
    print(f"Средний Acc:    {avg_acc:.3f}")
    print(f"Средний Sharpe: {avg_sharpe:.2f}")
    print(f"Окон где модель обогнала BH: {wins}/{len(results)}")
    print(f"Общая альфа: {total_alpha:+.1f}%")

    # Feature importance
    print("\n─── Feature Importance (топ-10) ───")
    imp = sorted(zip(feat_cols, model.feature_importances_),
                 key=lambda x: -x[1])[:10]
    for name, val in imp:
        bar = "█" * int(val * 200)
        print(f"  {name:<22}{val:>6.3f}  {bar}")

    # Финальный вердикт
    print("\n" + "=" * 95)
    if avg_auc > 0.55 and avg_sharpe > 0.5 and wins > len(results) / 2:
        print("  ✅ ЕСТЬ СИГНАЛ: AUC > 0.55, Sharpe > 0.5, обгон BH в большинстве окон")
    elif avg_auc > 0.52:
        print("  ⚠️ СЛАБЫЙ СИГНАЛ: AUC выше монетки, но нужен больший датасет")
    else:
        print("  ❌ НЕТ СИГНАЛА: модель на уровне монетки. Overfitting или мало данных.")

    print("  ⚠️ Помни: 365 дней — мало. Реальный тест требует 3-5 лет данных.")
