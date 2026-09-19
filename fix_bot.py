from pathlib import Path

p = Path("trading_bot.py")
src = p.read_text()

# --- 1. Найти и заменить функцию load_price_cache полностью ---
start_marker = "def load_price_cache(sym):"
end_marker = "def funding_signal"

i = src.find(start_marker)
j = src.find(end_marker)
if i == -1 or j == -1:
    print("WARN: маркеры не найдены")
    print(f"  start_marker found: {i != -1}")
    print(f"  end_marker found:   {j != -1}")
    raise SystemExit(1)

new_func = '''def load_price_cache(sym):
    """Универсальный загрузчик npz (o/h/l/c/v + синтетический индекс времени)."""
    p = DATA/f"cache/{sym}_1h_20000.npz"
    if not p.exists(): return None
    d = np.load(p)
    keys = set(d.files)
    n = len(d[d.files[0]])

    # Время: если нет ts/time — синтетика с шагом 1h
    tkey = None
    for cand in ["ts", "timestamp", "time", "date", "open_time"]:
        if cand in keys:
            tkey = cand; break
    if tkey is None:
        ts = pd.date_range(end=pd.Timestamp.now().floor("h"),
                           periods=n, freq="1h", tz=None)
    else:
        raw = d[tkey]
        if raw.dtype.kind in "iu" and raw.max() > 1e12:
            ts = pd.to_datetime(raw, unit="ms", utc=True).tz_localize(None)
        elif raw.dtype.kind in "iu" and raw.max() > 1e9:
            ts = pd.to_datetime(raw, unit="s", utc=True).tz_localize(None)
        else:
            ts = pd.to_datetime(raw)
            if getattr(ts, "tz", None) is not None:
                ts = ts.tz_convert(None)

    # Ищем o/h/l/c/v либо open/high/low/close/volume
    def pick(names):
        for nm in names:
            if nm in keys: return d[nm]
        return None

    o = pick(["o", "open", "open_price"])
    h = pick(["h", "high", "high_price"])
    l = pick(["l", "low", "low_price"])
    c = pick(["c", "close", "close_price"])
    v = pick(["v", "volume", "vol"])

    if c is None:
        return None
    if h is None: h = c
    if l is None: l = c
    if o is None: o = c
    if v is None: v = np.zeros(n)

    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=ts)
    return df


'''

src = src[:i] + new_func + src[j:]

# --- 2. Убрать Gold (PAXG) из PAIRS ---
old_pairs_start = src.find("PAIRS = {")
old_pairs_end = src.find("}", old_pairs_start) + 1
if old_pairs_start != -1 and old_pairs_end > 0:
    new_pairs = '''PAIRS = {
    "BTCUSDT": {"name": "Bitcoin",  "icon": "B", "color": "#f7931a"},
    "ETHUSDT": {"name": "Ethereum", "icon": "E", "color": "#627eea"},
    "XRPUSDT": {"name": "Ripple",   "icon": "X", "color": "#00aae4"},
    "SOLUSDT": {"name": "Solana",   "icon": "S", "color": "#9945ff"},
}'''
    src = src[:old_pairs_start] + new_pairs + src[old_pairs_end:]
    print("PAIRS обновлён: убран Gold (PAXG), добавлен Solana")

p.write_text(src)
print("OK: trading_bot.py обновлён")
