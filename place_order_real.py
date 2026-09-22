"""
BINANCE REAL PLACER — $15 MODE
Только XRP / LINK / DOGE
"""
import json, time, hmac, hashlib, requests
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode

ROOT = Path("/Users/andromeda/crypto-carry")
ENV_FILE = ROOT / ".env.real"
STATE_FILE = ROOT / "live_state.json"
ORDERS_LOG = ROOT / "logs" / "real_orders.jsonl"
PLACED_FILE = ROOT / "logs" / "real_placed.json"
KILL_SWITCH = ROOT / "STOP_TRADING"
BASE_URL = "https://fapi.binance.com"

ALLOWED_SYMBOLS = ["XRPUSDT", "LINKUSDT", "DOGEUSDT"]
LEVERAGE = 3
NOTIONAL_PER_TRADE = 25
POLL_SEC = 60
STOP_LOSS_PCT = 0.10
MAX_POSITIONS = 5

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_env():
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
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

def get_balance(ak, sk):
    r = signed("GET", "/fapi/v2/balance", ak, sk)
    if r.status_code != 200: return None
    for b in r.json():
        if b["asset"] == "USDT": return float(b["balance"])
    return None

def get_open_positions(ak, sk):
    """Возвращает {symbol: amt} для открытых позиций."""
    r = signed("GET", "/fapi/v2/positionRisk", ak, sk)
    if r.status_code != 200: return {}
    out = {}
    for p in r.json():
        amt = abs(float(p.get("positionAmt", 0)))
        if amt > 0:
            out[p["symbol"]] = amt
    return out

def set_leverage(sym, ak, sk):
    signed("POST", "/fapi/v1/leverage", ak, sk, {"symbol": sym, "leverage": LEVERAGE})

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

from decimal import Decimal, ROUND_CEILING, ROUND_DOWN

def _decimals(s):
    s_str = f"{s:.10f}".rstrip('0')
    return len(s_str.split('.')[1]) if '.' in s_str else 0

def round_step(v, s):
    if s <= 0: return v
    d = Decimal(str(v)) / Decimal(str(s))
    d = d.to_integral_value(rounding=ROUND_DOWN)
    return float(d * Decimal(str(s)))

def round_step_up(v, s):
    if s <= 0: return v
    d = Decimal(str(v)) / Decimal(str(s))
    d = d.to_integral_value(rounding=ROUND_CEILING)
    result = float(d * Decimal(str(s)))
    return round(result, _decimals(s))

def market_order(sym, side, qty, ak, sk):
    # Округляем до 8 знаков максимум
    qty = float(f"{qty:.8f}".rstrip('0').rstrip('.'))
    r = signed("POST", "/fapi/v1/order", ak, sk, {
        "symbol": sym, "side": side, "type": "MARKET", "quantity": qty,
    })
    if r.status_code != 200:
        log(f"   market err: {r.text[:150]}"); return None
    return r.json()

def stop_market(sym, side, stop, qty, ak, sk):
    """STOP_MARKET через новый Algo endpoint Binance."""
    stop_price = float(f"{stop:.8f}".rstrip('0').rstrip('.'))
    r = signed("POST", "/fapi/v1/algoOrder", ak, sk, {
        "algoType": "CONDITIONAL",
        "symbol": sym,
        "side": side,
        "type": "STOP_MARKET",
        "triggerPrice": stop_price,
        "quantity": qty,
        "reduceOnly": "true",
        "workingType": "CONTRACT_PRICE",
        "priceProtect": "TRUE",
    })
    if r.status_code != 200:
        log(f"   stop err: {r.text[:200]}"); return None
    return r.json()

def take_profit(sym, side, stop, qty, ak, sk):
    """TAKE_PROFIT_MARKET через Algo endpoint."""
    take_price = float(f"{stop:.8f}".rstrip('0').rstrip('.'))
    r = signed("POST", "/fapi/v1/algoOrder", ak, sk, {
        "algoType": "CONDITIONAL",
        "symbol": sym,
        "side": side,
        "type": "TAKE_PROFIT_MARKET",
        "triggerPrice": take_price,
        "quantity": qty,
        "reduceOnly": "true",
        "workingType": "CONTRACT_PRICE",
        "priceProtect": "TRUE",
    })
    if r.status_code != 200:
        log(f"   tp err: {r.text[:200]}"); return None
    return r.json()

def main():
    log("=" * 60)
    log("  BINANCE REAL PLACER — $15 MODE")
    log("=" * 60)

    try:
        ak, sk = load_env()
    except Exception as e:
        log(f"{e}"); return

    bal = get_balance(ak, sk)
    if bal is None:
        log("Не могу получить баланс"); return
    log(f"Баланс: ${bal:.2f}")
    log(f"Символы: {ALLOWED_SYMBOLS}")
    log(f"Notional: ${NOTIONAL_PER_TRADE} | Плечо: {LEVERAGE}x | Max: {MAX_POSITIONS}")
    log(f"Стоп при -{STOP_LOSS_PCT*100:.0f}% (${bal*(1-STOP_LOSS_PCT):.2f})")
    log("")

    start_bal = bal
    placed = json.loads(PLACED_FILE.read_text()) if PLACED_FILE.exists() else {}

    while True:
        if KILL_SWITCH.exists():
            log("KILL SWITCH — остановка"); return

        cur_bal = get_balance(ak, sk)
        if cur_bal and (start_bal - cur_bal) / start_bal > STOP_LOSS_PCT:
            log(f"Убыток >{STOP_LOSS_PCT*100:.0f}% — остановка"); return

        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            positions = state.get("positions", {})
            active = [k for k in placed if not placed[k].get("closed")]
            log(f"Сигналов: {len(positions)} | Открыто: {len(active)}/{MAX_POSITIONS}")

            # Динамический расчёт: сколько позиций можем открыть
            free_bal = get_balance(ak, sk) or 0
            margin_per_pos = NOTIONAL_PER_TRADE / LEVERAGE
            max_by_balance = int(free_bal * 0.7 / margin_per_pos) if margin_per_pos > 0 else 0
            max_this_cycle = min(MAX_POSITIONS, max_by_balance)
            log(f"   [Бал: ${free_bal:.2f} | Маржа/поз: ${margin_per_pos:.2f} | Можем открыть: {max_this_cycle}]")

            # Получаем открытые позиции ОДИН раз перед циклом
            open_positions = get_open_positions(ak, sk)
            if open_positions:
                log(f"   [Открыто на Binance: {list(open_positions.keys())}]")

            for sym, pos in positions.items():
                if sym not in ALLOWED_SYMBOLS: continue
                key = f"{sym}_{pos['entry_ts']}"
                if key in placed: continue
                if sym in open_positions:
                    log(f"{sym}: уже открыта на Binance — пропуск")
                    continue
                if len(active) >= max_this_cycle:
                    log(f"{sym}: лимит по балансу"); break

                info = get_symbol_info(sym)
                if not info:
                    log(f"{sym}: no symbol info"); continue

                # Получаем ТЕКУЩУЮ цену для расчёта qty
                r_price = requests.get(f"{BASE_URL}/fapi/v1/ticker/price?symbol={sym}", timeout=5)
                if r_price.status_code == 200:
                    current_price = float(r_price.json()["price"])
                else:
                    current_price = pos["entry"]
                # qty с запасом +5% и округление ВВЕРХ
                target_notional = NOTIONAL_PER_TRADE * 1.05
                qty = round_step_up(target_notional / current_price, info["step_size"])
                log(f"   price=${current_price:.4f} qty={qty} notional~${qty * current_price:.2f}")
                if qty <= 0:
                    log(f"{sym}: qty=0"); continue

                log(f"{sym} LONG @ ${pos['entry']} qty={qty}")
                set_leverage(sym, ak, sk)
                time.sleep(0.3)

                order = market_order(sym, "BUY", qty, ak, sk)
                if not order: continue
                log(f"   Market #{order.get('orderId')}")
                time.sleep(0.5)

                stop_p = round_step(pos["stop"], info["tick_size"])
                stop_ord = stop_market(sym, "SELL", stop_p, qty, ak, sk)
                time.sleep(0.5)

                take_p = round_step(pos["take"], info["tick_size"])
                tp_ord = take_profit(sym, "SELL", take_p, qty, ak, sk)

                rec = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "symbol": sym, "entry": pos["entry"],
                    "stop": pos["stop"], "take": pos["take"],
                    "qty": qty, "notional": round(qty * pos["entry"], 2),
                    "market_id": order.get("orderId"),
                    "stop_id": stop_ord.get("orderId") if stop_ord else None,
                    "tp_id": tp_ord.get("orderId") if tp_ord else None,
                    "closed": False,
                }
                with ORDERS_LOG.open("a") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
                placed[key] = rec
                PLACED_FILE.write_text(json.dumps(placed, indent=2, default=str))
                active.append(key)
                log(f"   Orders placed")

        log(f"Sleep {POLL_SEC}s...\n")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
