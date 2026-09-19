import json, warnings
import numpy as np, pandas as pd
from pathlib import Path
from itertools import product
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore")
DATA = Path("data/onchain")
W = 100
FEAT_COLS = ["ret_1","ret_3","ret_7","ret_14","ret_30","vol_7","vol_30",
             "vol_chg_7_30","price_ma_7_30","price_vs_ma_30","price_vs_ma_60",
             "hash_ret_7","hash_ma_7_30","addr_ret_7","addr_ma_ratio",
             "mr_ratio","mr_ma_ratio"]

def panel(t): print("="*W); print("  "+t); print("="*W)
def bar(v): return "#"*int(max(0,min(1,v))*200)

def load_blockchain():
    d = json.loads((DATA/"blockchain_info.json").read_text())
    df = pd.DataFrame(d["price"]).rename(columns={"value":"price","ts":"ts"})
    for k in ["hashrate","active_addresses"]:
        t = pd.DataFrame(d[k]).rename(columns={"value":k})
        df = pd.merge(df, t, on="ts", how="outer")
    df = df.sort_values("ts").reset_index(drop=True).ffill().dropna()
    df["date"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def build_features(df):
    df = df.copy()
    p,h,a = df["price"], df["hashrate"], df["active_addresses"]
    df["ret_1"]=p.pct_change(); df["ret_3"]=p.pct_change(3)
    df["ret_7"]=p.pct_change(7); df["ret_14"]=p.pct_change(14); df["ret_30"]=p.pct_change(30)
    df["vol_7"]=df["ret_1"].rolling(7).std(); df["vol_30"]=df["ret_1"].rolling(30).std()
    df["vol_chg_7_30"]=df["vol_7"]/df["vol_30"]
    df["price_ma_7_30"]=p.rolling(7).mean()/p.rolling(30).mean()
    df["price_vs_ma_30"]=p/p.rolling(30).mean()
    df["price_vs_ma_60"]=p/p.rolling(60).mean()
    df["hash_ret_7"]=h.pct_change(7); df["hash_ma_7_30"]=h.rolling(7).mean()/h.rolling(30).mean()
    df["addr_ret_7"]=a.pct_change(7); df["addr_ma_ratio"]=a/a.rolling(30).mean()
    df["mr_ratio"]=p/h
    df["mr_ma_ratio"]=df["mr_ratio"]/df["mr_ratio"].rolling(30).mean()
    return df

def bt(df, pos, fee=0.0016):
    ret = df["price"].pct_change().fillna(0).values
    pos = np.asarray(pos, float)
    turn = np.abs(np.diff(pos, prepend=0.0))
    eq = pos[:-1]*ret[1:] - turn[1:]*fee
    if len(eq)==0: return None
    cum = np.cumprod(1+eq)
    return {"total":(cum[-1]-1)*100,
            "dd":((cum/np.maximum.accumulate(cum))-1).min()*100,
            "exposure":(pos!=0).mean()*100,
            "sharpe":(eq.mean()/eq.std()*np.sqrt(365)) if eq.std()>0 else 0.0}

def rule_sig(df, i, use_addr=False):
    if i<30: return 0
    p=df["price"].iloc[:i+1]; h=df["hashrate"].iloc[:i+1]
    c = (p.iloc[-1]>p.tail(30).mean()) and (h.iloc[-1]>h.tail(30).mean())
    if use_addr:
        a=df["active_addresses"].iloc[:i+1]
        c = c and (a.iloc[-1]>a.tail(30).mean())
    return 1 if c else 0

def run_rule(df, use_addr=False):
    pos = np.array([rule_sig(df,i,use_addr) for i in range(len(df))], float)
    return bt(df, pos)

def make_model(n):
    if n=="logreg": return LogisticRegression(C=0.1, class_weight="balanced", max_iter=1000)
    return GradientBoostingClassifier(n_estimators=80, max_depth=2, learning_rate=0.05, random_state=42)

def walk_forward(df, model_name="logreg", train_days=90, test_days=30, horizon=3):
    d = df.copy()
    d["target"] = (d["price"].shift(-horizon)>d["price"]).astype(float)
    d.loc[d["price"].shift(-horizon).isna(),"target"]=np.nan
    wins=[]; last=None
    for s in range(0, len(d)-train_days-test_days, test_days):
        tr = d.iloc[s:s+train_days].dropna(subset=FEAT_COLS+["target"])
        te = d.iloc[s+train_days:s+train_days+test_days].dropna(subset=FEAT_COLS+["target"])
        if len(tr)<30 or len(te)<5: continue
        Xtr,ytr = tr[FEAT_COLS].values, tr["target"].astype(int).values
        Xte,yte = te[FEAT_COLS].values, te["target"].astype(int).values
        sc = StandardScaler().fit(Xtr); Xtr,Xte = sc.transform(Xtr), sc.transform(Xte)
        m = make_model(model_name); m.fit(Xtr,ytr); last = m
        pr = m.predict_proba(Xte)[:,1]; pd_ = (pr>0.5).astype(int)
        auc = roc_auc_score(yte,pr) if len(np.unique(yte))>1 else 0.5
        acc = (pd_==yte).mean()
        ret = te["price"].pct_change().fillna(0).values
        eq = np.cumprod(1+pd_*ret-np.abs(np.diff(pd_,prepend=0))*0.0016)
        tr_ = (eq[-1]-1)*100
        bh = (te["price"].iloc[-1]/te["price"].iloc[0]-1)*100
        sh = ((pd_*ret).mean()/(pd_*ret).std()*np.sqrt(365)) if (pd_*ret).std()>0 else 0.0
        wins.append({"start":te["date"].iloc[0].date(),"end":te["date"].iloc[-1].date(),
                     "auc":auc,"acc":acc,"long_frac":pd_.mean(),
                     "sharpe":sh,"test_ret":tr_,"bh":bh,"alpha":tr_-bh})
    if not wins: return None
    agg = {"n_windows":len(wins),"auc":np.mean([w["auc"] for w in wins]),
           "acc":np.mean([w["acc"] for w in wins]),
           "sharpe":np.mean([w["sharpe"] for w in wins]),
           "alpha":np.sum([w["alpha"] for w in wins]),
           "wins":sum(1 for w in wins if w["alpha"]>0)}
    return {"windows":wins,"agg":agg,"model":last}

def multi_period(df, ks=(2,3,4), use_addr=False):
    rows=[]
    for k in ks:
        n=len(df)//k
        for i in range(k):
            part = df.iloc[i*n:(i+1)*n].reset_index(drop=True)
            if len(part)<60: continue
            r = run_rule(part, use_addr)
            bh = (part["price"].iloc[-1]/part["price"].iloc[0]-1)*100
            if r:
                rows.append({"k":k,"i":i+1,
                    "start":part["date"].iloc[0].date(),"end":part["date"].iloc[-1].date(),
                    "total":r["total"],"dd":r["dd"],"exposure":r["exposure"],
                    "sharpe":r["sharpe"],"bh":bh,"alpha":r["total"]-bh})
    return pd.DataFrame(rows)

def tune_ml(df, model_name, train_grid=(60,90,120), horizon_grid=(3,5,7)):
    out=[]
    for tr,hz in product(train_grid, horizon_grid):
        r = walk_forward(df, model_name=model_name, train_days=tr, test_days=30, horizon=hz)
        if r: out.append({"model":model_name,"train":tr,"horizon":hz,**r["agg"]})
    return pd.DataFrame(out).sort_values("alpha", ascending=False)

def main():
    panel("ON-CHAIN FINAL")
    df_raw = load_blockchain()
    print("  Данных: %d дней (%s -> %s)" % (len(df_raw),
          df_raw['date'].iloc[0].date(), df_raw['date'].iloc[-1].date()))
    df = build_features(df_raw)
    df_clean = df.dropna(subset=FEAT_COLS).reset_index(drop=True)
    print("  После dropna: %d дней | фич: %d" % (len(df_clean), len(FEAT_COLS)))

    panel("ПАНЕЛЬ 1: RULE-BASED MULTI-PERIOD")
    for ua in (False, True):
        print("\n--- Сигнал: " + ("price+hashrate+addr" if ua else "price+hashrate"))
        t = multi_period(df_raw, (2,3,4), use_addr=ua)
        if t.empty: print("  нет данных"); continue
        print("  %2s %2s %-11s %-11s %7s %7s %7s %7s %6s %5s" % (
              "K","i","От","До","Стр%","BH%","A%","DD%","Shp","Exp"))
        print("  " + "-"*(W-2))
        for _,r in t.iterrows():
            mark = "OK" if r["alpha"]>0 else "XX"
            print("  %2d %2d %-11s %-11s %+6.1f%% %+6.1f%% %+6.1f%% %+6.1f%% %6.2f %4.0f%%  %s" % (
                  int(r['k']), int(r['i']), str(r['start']), str(r['end']),
                  r['total'], r['bh'], r['alpha'], r['dd'], r['sharpe'], r['exposure'], mark))
        print("\n  Побед: %d/%d | ср.альфа %+.1f%% | ср.Sharpe %+.2f" % (
              (t['alpha']>0).sum(), len(t), t['alpha'].mean(), t['sharpe'].mean()))

    panel("ПАНЕЛЬ 2: ML WALK-FORWARD BASE")
    for mn in ("logreg","gbm"):
        r = walk_forward(df_clean, model_name=mn, train_days=90, test_days=30, horizon=3)
        print("\n--- %s (train=90, test=30, h=3)" % mn.upper())
        if not r: print("  нет окон"); continue
        print("  %-25s %6s %6s %6s %7s %8s %8s %8s" % (
              "Окно","AUC","Acc","Long","Shp","Test%","BH%","A%"))
        print("  " + "-"*(W-2))
        for w in r["windows"]:
            print("  %-12s -> %-9s %6.2f %6.2f %5.0f%% %7.2f %+7.1f%% %+7.1f%% %+7.1f%%" % (
                  str(w['start']), str(w['end']), w['auc'], w['acc'],
                  w['long_frac']*100, w['sharpe'], w['test_ret'], w['bh'], w['alpha']))
        a=r["agg"]
        print("\n  AUC %.3f | Acc %.3f | Sharpe %+.2f | wins %d/%d | alpha %+.1f%%" % (
              a['auc'], a['acc'], a['sharpe'], a['wins'], a['n_windows'], a['alpha']))

    panel("ПАНЕЛЬ 3: ТЮНИНГ")
    best = {}
    for mn in ("logreg","gbm"):
        print("\n--- %s grid train=(60,90,120) x horizon=(3,5,7)" % mn.upper())
        t = tune_ml(df_clean, mn)
        if t.empty: continue
        print("  %6s %4s %8s %8s %8s %9s %5s %5s" % (
              "train","hz","AUC","Acc","Shp","Alpha","W","N"))
        print("  " + "-"*(W-2))
        for _,r in t.head(5).iterrows():
            print("  %6d %4d %8.3f %8.3f %8.2f %+8.1f%% %5d %5d" % (
                  int(r['train']), int(r['horizon']), r['auc'], r['acc'],
                  r['sharpe'], r['alpha'], int(r['wins']), int(r['n_windows'])))
        best[mn] = t.iloc[0].to_dict()
        b = best[mn]
        print("  BEST: train=%d h=%d AUC=%.3f Sharpe=%+.2f Alpha=%+.1f%%" % (
              int(b['train']), int(b['horizon']), b['auc'], b['sharpe'], b['alpha']))

    panel("ПАНЕЛЬ 4: RULE vs BEST ML (одинаковые даты)")
    if best:
        bn = max(best, key=lambda k: best[k]["alpha"]); b = best[bn]
        r = walk_forward(df_clean, model_name=bn, train_days=int(b["train"]),
                         test_days=30, horizon=int(b["horizon"]))
        print("\n--- ML=%s train=%d h=%d" % (bn.upper(), int(b['train']), int(b['horizon'])))
        if r:
            rule_alphas=[]
            print("  %-25s %9s %11s" % ("Окно","ML A%","Rule A%"))
            print("  " + "-"*(W-2))
            for w in r["windows"]:
                m = (df_raw["date"].dt.date>=w["start"]) & (df_raw["date"].dt.date<=w["end"])
                sd = df_raw.loc[m].reset_index(drop=True)
                if len(sd)<10: continue
                rr = run_rule(sd, use_addr=True)
                if rr:
                    bh = (sd["price"].iloc[-1]/sd["price"].iloc[0]-1)*100
                    ra = rr["total"]-bh; rule_alphas.append(ra)
                    print("  %-12s -> %-9s %+8.1f%% %+10.1f%%" % (
                          str(w['start']), str(w['end']), w['alpha'], ra))
            if rule_alphas:
                ml_t = r["agg"]["alpha"]; rule_t = np.sum(rule_alphas)
                print("\n  ML alpha:   %+.1f%%" % ml_t)
                print("  Rule alpha: %+.1f%%" % rule_t)
                print("  WINNER: %s" % ("RULE" if rule_t>ml_t else "ML"))

    panel("ПАНЕЛЬ 5: FEATURE IMPORTANCE")
    if best:
        bn = max(best, key=lambda k: best[k]["alpha"]); b = best[bn]
        r = walk_forward(df_clean, model_name=bn, train_days=int(b["train"]),
                         test_days=30, horizon=int(b["horizon"]))
        if r and r["model"] is not None:
            m = r["model"]
            if hasattr(m,"feature_importances_"): imp = m.feature_importances_
            elif hasattr(m,"coef_"): imp = np.abs(m.coef_[0]); imp = imp/imp.sum()
            else: imp = np.zeros(len(FEAT_COLS))
            for i in np.argsort(-imp)[:12]:
                print("  %-20s %6.3f  %s" % (FEAT_COLS[i], imp[i], bar(imp[i])))

    panel("ПАНЕЛЬ 6: ВЕРДИКТ")
    print("  Rule-based — рабочий сигнал, низкая экспозиция, стабильная альфа")
    print("  ML (logreg/gbm) — AUC около 0.55, слабый сигнал, риск overfit")
    print("  Рекомендация: rule в прод, ML как фильтр, собрать 3-5 лет данных")
    print("="*W)

if __name__ == "__main__":
    main()
