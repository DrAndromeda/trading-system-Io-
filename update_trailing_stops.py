"""
TRAILING STOP UPDATER
=====================
Каждые 60 сек проверяет открытые позиции на Binance:
- Если прибыль >= 1%, подтягивает STOP_MARKET.
- Отменяет старый, ставит новый.
"""
import json, time, hmac, hashlib, requests, math
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
from decimal import Decimal, ROUND_DOWN

ROOT = Path("/Users/andromeda/crypto-carry")
ENV_FILE = ROOT / ".env.real"
PLACED_FILE = ROOT / "logs" / "real_placed.json"
STATE_FILE = ROOT / "live_state.json"
TRAILING_LOG = ROOT / "logs" / "trailing.log"
KILL_SWITCH = ROOT / "STOP_TRADING"

BASE_URL = "https://fapi.binance.com"
POLL_SEC = 60
TRAIL_ACTIVATE_PCT = 1.0    # активируется при +1%
TRAIL_DISTANCE_ATR = 0.7    # стоп = 0.7 * ATR от текущей цены

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with TRAILING_LOG.open("a") as f:
        f.write(line + "\n")

def load_env():
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env["BINANCE_REAL_API_KEY"], env["BINANCE_REAL_SECRET_KEY"]

def signed(method, path, ak, sk, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    q = urlencode(params)
    sig = hmac.new(sk.encode(), q.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{q}&signature={sig}"
    h = {"X-MBX-APIKEY": ak}
    if method == "GET": return requests.get(url, headers=h, timeout=10)
    if method == "POST": return requests.post(url, headers=h, timeout=10)
    if method == "DELETE": return requests.delete(url, headers=h, timeout=10)

def get_price(sym):
    r = requests.get(f"{BASE_URL}/fapi/v1/ticker/price?symbol={sym}", timeout=5)
    return float(r.json()["price"]) if r.status_code == 200 else None

def get_symbol_info(sym):
    r = requests.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=10)
    if r.status_code != 200: return None
    for s in r.json()["symbols"]:
        if s["symbol"] == sym:
            step = tick = None
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE": step = float(f["stepSize"])
                if f["filterType"] == "PRICE_FILTER": tick = float(f["tickSize"])
            return {"step_size": step or 0.001, "tick_size": tick or 0.01}
    return None

def round_step_down(v, s):
    if s <= 0: return v
    d = Decimal(str(v)) / Decimal(str(s))
    d = d.to_integral_value(rounding=ROUND_DOWN)
    return float(d * Decimal(str(s)))

def get_open_orders(sym, ak, sk):
    """Algo ордера (стопы/тейки) через новый endpoint."""
    r = signed("GET", "/fapi/v1/openAlgoOrders", ak, sk, {"symbol": sym})
    return r.json() if r.status_code == 200 else []

def cancel_order(sym, algo_id, ak, sk):
    """Отмена Algo ордера."""
    r = signed("DELETE", "/fapi/v1/algoOrder", ak, sk, {"algoId": algo_id})
    return r.status_code == 200

def place_stop_market(sym, stop_price, qty, ak, sk):
    """STOP_MARKET через Algo endpoint."""
    r = signed("POST", "/fapi/v1/algoOrder", ak, sk, {
        "algoType": "CONDITIONAL",
        "symbol": sym, "side": "SELL", "type": "STOP_MARKET",
        "triggerPrice": stop_price, "quantity": qty, "reduceOnly": "true",
        "workingType": "CONTRACT_PRICE", "priceProtect": "TRUE",
    })
    return r.json() if r.status_code == 200 else None

def get_atr_from_state(sym, state):
    """Получаем atr_pct из последнего сигнала."""
    for s in reversed(state.get("signals", [])):
        if s["symbol"] == sym and "atr_pct" in s:
            return s["atr_pct"] / 100  # в десятичную
    return 0.005  # fallback 0.5%

def main():
    log("=" * 60)
    log("  TRAILING STOP UPDATER — STARTED")
    log("=" * 60)

    try:
        ak, sk = load_env()
    except Exception as e:
        log(f"{e}"); return

    # Храним последнюю передвинутую цену стопа, чтобы не спамить Binance
    trailing_state_file = ROOT / "logs" / "trailing_state.json"
    trail_state = {}
    if trailing_state_file.exists():
        trail_state = json.loads(trailing_state_file.read_text())

    while True:
        if KILL_SWITCH.exists():
            log("KILL SWITCH — остановка"); return

        if not PLACED_FILE.exists():
            log("Нет real_placed.json — ждём")
            time.sleep(POLL_SEC); continue

        placed = json.loads(PLACED_FILE.read_text())
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

        # Получаем открытые позиции с Binance
        r_pos = signed("GET", "/fapi/v2/positionRisk", ak, sk)
        if r_pos.status_code != 200:
            log(f"pos risk err: {r_pos.text[:100]}")
            time.sleep(POLL_SEC); continue
        open_positions = {p["symbol"]: p for p in r_pos.json() if abs(float(p["positionAmt"])) > 0}

        log(f"Открытых позиций: {len(open_positions)}")

        for key, rec in placed.items():
            if rec.get("closed"): continue
            sym = rec["symbol"]
            if sym not in open_positions: continue

            entry = float(rec["entry"])
            current_stop = float(rec["stop"])
            qty = abs(float(open_positions[sym]["positionAmt"]))
            cur_price = get_price(sym)
            if not cur_price or qty <= 0: continue

            profit_pct = (cur_price - entry) / entry * 100

            # Активация только после +1%
            if profit_pct < TRAIL_ACTIVATE_PCT:
                log(f"  {sym}: +{profit_pct:.2f}% (нужно +{TRAIL_ACTIVATE_PCT}%) — ждём")
                continue

            # Считаем новый стоп по ATR
            atr_pct = get_atr_from_state(sym, state)
            new_stop = cur_price * (1 - TRAIL_DISTANCE_ATR * atr_pct)

            # Не двигаем вниз
            if new_stop <= current_stop:
                log(f"  {sym}: стоп не двигается (${current_stop:.6f} → ${new_stop:.6f})")
                continue

            # Округляем к tick size
            info = get_symbol_info(sym)
            if not info: continue
            new_stop_r = round_step_down(new_stop, info["tick_size"])

            # Не двигаем если уже стоит близко (<0.05%)
            last_stop = trail_state.get(key, {}).get("last_stop", 0)
            if last_stop > 0 and abs(new_stop_r - last_stop) / last_stop < 0.0005:
                continue

            log(f"  {sym}: +{profit_pct:.2f}% → двигаем стоп ${current_stop:.6f} → ${new_stop_r:.6f}")

            # 1. Отменяем все STOP_MARKET на эту монету
            open_orders = get_open_orders(sym, ak, sk)
            cancelled = 0
            for o in open_orders:
                if o.get("orderType") == "STOP_MARKET":
                    if cancel_order(sym, o["algoId"], ak, sk):
                        cancelled += 1
            log(f"    отменено старых: {cancelled}")

            time.sleep(0.3)

            # 2. Ставим новый
            new_ord = place_stop_market(sym, new_stop_r, qty, ak, sk)
            if new_ord:
                log(f"    ✅ новый STOP @ ${new_stop_r:.6f} (#{new_ord.get('orderId')})")
                rec["stop"] = new_stop_r
                rec["stop_id"] = new_ord.get("orderId")
                trail_state[key] = {"last_stop": new_stop_r, "ts": datetime.now(timezone.utc).isoformat()}
            else:
                log(f"    ❌ не удалось поставить новый стоп")

        # Сохраняем изменения
        PLACED_FILE.write_text(json.dumps(placed, indent=2, default=str))
        trailing_state_file.write_text(json.dumps(trail_state, indent=2))

        log(f"Sleep {POLL_SEC}s...\n")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
