"""
BINANCE TESTNET ORDER PLACER
=============================
Читает сигналы из live_state.json и отправляет ордера на Binance Futures Testnet.

Безопасно: только Testnet, никаких реальных денег.
"""
import os
import json
import time
import hmac
import hashlib
import requests
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode

ROOT = Path("/Users/andromeda/crypto-carry")
ENV_FILE = ROOT / ".env.testnet"
STATE_FILE = ROOT / "live_state.json"
ORDERS_LOG = ROOT / "logs" / "testnet_orders.jsonl"
PLACED_FILE = ROOT / "logs" / "testnet_placed.json"  # чтобы не дублировать

BASE_URL = "https://testnet.binancefuture.com"

# === Настройки ===
LEVERAGE = 3              # плечо на Testnet
NOTIONAL_PER_TRADE = 500  # $500 notional на сделку (виртуальных)
POLL_SEC = 60             # проверка каждые 60 сек


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_env():
    """Читает API keys из .env.testnet."""
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"❌ Не найден {ENV_FILE}")
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    api_key = env.get("BINANCE_TESTNET_API_KEY")
    secret_key = env.get("BINANCE_TESTNET_SECRET_KEY")
    if not api_key or not secret_key or "вставь" in api_key.lower() or "сюда" in api_key.lower():
        raise ValueError("❌ Ключи не заполнены в .env.testnet")
    return api_key, secret_key


def signed_request(method, path, api_key, secret_key, params=None):
    """Отправляет подписанный запрос к Binance Futures Testnet."""
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    query = urlencode(params)
    signature = hmac.new(secret_key.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}
    if method == "GET":
        r = requests.get(url, headers=headers, timeout=10)
    elif method == "POST":
        r = requests.post(url, headers=headers, timeout=10)
    elif method == "DELETE":
        r = requests.delete(url, headers=headers, timeout=10)
    else:
        raise ValueError(method)
    return r


def get_balance(api_key, secret_key):
    r = signed_request("GET", "/fapi/v2/balance", api_key, secret_key)
    if r.status_code != 200:
        return None
    for b in r.json():
        if b["asset"] == "USDT":
            return float(b["balance"])
    return None


def set_leverage(symbol, leverage, api_key, secret_key):
    r = signed_request("POST", "/fapi/v1/leverage", api_key, secret_key,
                       {"symbol": symbol, "leverage": leverage})
    if r.status_code == 200:
        return True
    log(f"  ⚠️ leverage set failed: {r.text[:100]}")
    return False


def place_market_order(symbol, side, quantity, api_key, secret_key):
    r = signed_request("POST", "/fapi/v1/order", api_key, secret_key, {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": quantity,
    })
    if r.status_code == 200:
        return r.json()
    log(f"  ❌ market order failed: {r.text[:200]}")
    return None


def place_stop_market(symbol, side, stop_price, quantity, api_key, secret_key):
    r = signed_request("POST", "/fapi/v1/order", api_key, secret_key, {
        "symbol": symbol,
        "side": side,
        "type": "STOP_MARKET",
        "stopPrice": round(stop_price, 4),
        "quantity": quantity,
        "reduceOnly": "true",
    })
    if r.status_code == 200:
        return r.json()
    log(f"  ⚠️ stop order failed: {r.text[:150]}")
    return None


def place_take_profit(symbol, side, stop_price, quantity, api_key, secret_key):
    r = signed_request("POST", "/fapi/v1/order", api_key, secret_key, {
        "symbol": symbol,
        "side": side,
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": round(stop_price, 4),
        "quantity": quantity,
        "reduceOnly": "true",
    })
    if r.status_code == 200:
        return r.json()
    log(f"  ⚠️ take profit failed: {r.text[:150]}")
    return None


def get_symbol_info(symbol, api_key, secret_key):
    """Получает precision для symbol (шаг цены и количество)."""
    r = requests.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=10)
    if r.status_code != 200:
        return None
    for s in r.json()["symbols"]:
        if s["symbol"] == symbol:
            step_size = None
            tick_size = None
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step_size = float(f["stepSize"])
                if f["filterType"] == "PRICE_FILTER":
                    tick_size = float(f["tickSize"])
            return {
                "symbol": symbol,
                "step_size": step_size,
                "tick_size": tick_size,
                "min_qty": float(s.get("filters", [{}])[1].get("minQty", 0.001)) if len(s["filters"]) > 1 else 0.001,
            }
    return None


def round_step(value, step):
    if step <= 0:
        return value
    return round(value / step) * step


def load_placed():
    if PLACED_FILE.exists():
        return json.loads(PLACED_FILE.read_text())
    return {}


def save_placed(p):
    PLACED_FILE.write_text(json.dumps(p, indent=2, default=str))


def log_order(record):
    with ORDERS_LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def main_cycle(api_key, secret_key, placed):
    """Один цикл: проверяем сигналы, отправляем ордера."""
    if not STATE_FILE.exists():
        log("⏸ Нет live_state.json")
        return

    state = json.loads(STATE_FILE.read_text())
    positions = state.get("positions", {})
    balance = get_balance(api_key, secret_key)
    log(f"💰 Testnet Balance: ${balance:.2f} | Сигналов в state: {len(positions)}")

    # Открытых позиций на Testnet у нас пока нет (мы только отправляем)
    # Но если в state есть LONG — и он ещё не отправлен — шлём

    for sym, pos in positions.items():
        # Ключ уникальности сигнала: symbol + entry_ts
        key = f"{sym}_{pos['entry_ts']}"
        if key in placed:
            continue

        log(f"🚀 Новый сигнал: {sym} LONG @ ${pos['entry']}")
        log(f"   SL ${pos['stop']} | TP ${pos['take']}")

        # Сначала получаем precision
        info = get_symbol_info(sym, api_key, secret_key)
        if not info:
            log(f"   ❌ Symbol info not found for {sym}")
            continue

        step = info.get("step_size", 0.001)
        if step is None or step == 0:
            step = 0.001
        tick = info.get("tick_size", 0.01)
        if tick is None or tick == 0:
            tick = 0.01

        # Размер позиции
        qty = NOTIONAL_PER_TRADE / pos["entry"]
        qty = round_step(qty, step)

        if qty <= 0:
            log(f"   ❌ qty too small: {qty}")
            continue

        # Устанавливаем плечо
        set_leverage(sym, LEVERAGE, api_key, secret_key)
        time.sleep(0.3)

        # === 1. Market buy ===
        log(f"   📤 Market BUY {qty} {sym}...")
        order = place_market_order(sym, "BUY", qty, api_key, secret_key)
        time.sleep(0.5)

        if not order:
            continue

        log(f"   ✅ Market order: {order.get('orderId')}")

        # === 2. Stop Loss ===
        stop_rounded = round_step(pos["stop"], tick)
        log(f"   📤 STOP_MARKET SELL @ ${stop_rounded}...")
        stop_ord = place_stop_market(sym, "SELL", stop_rounded, qty, api_key, secret_key)
        time.sleep(0.5)

        # === 3. Take Profit ===
        take_rounded = round_step(pos["take"], tick)
        log(f"   📤 TAKE_PROFIT_MARKET SELL @ ${take_rounded}...")
        tp_ord = place_take_profit(sym, "SELL", take_rounded, qty, api_key, secret_key)

        # Записываем в лог
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": sym,
            "side": "LONG",
            "entry": pos["entry"],
            "stop": pos["stop"],
            "take": pos["take"],
            "qty": qty,
            "notional": round(qty * pos["entry"], 2),
            "leverage": LEVERAGE,
            "market_order_id": order.get("orderId") if order else None,
            "stop_order_id": stop_ord.get("orderId") if stop_ord else None,
            "tp_order_id": tp_ord.get("orderId") if tp_ord else None,
        }
        log_order(record)
        placed[key] = record
        save_placed(placed)
        log(f"   ✅ Orders placed for {sym}")


def main():
    log("=" * 60)
    log("  BINANCE TESTNET ORDER PLACER")
    log("=" * 60)

    try:
        api_key, secret_key = load_env()
    except Exception as e:
        log(f"❌ {e}")
        log("   Проверь ~/crypto-carry/.env.testnet")
        return

    log(f"🔑 API Key: {api_key[:8]}...{api_key[-4:]}")
    balance = get_balance(api_key, secret_key)
    if balance is None:
        log("❌ Не могу получить баланс — проверь ключи")
        return
    log(f"💰 Баланс Testnet: ${balance:.2f}")

    placed = load_placed()
    log(f"📦 Уже отправлено ордеров: {len(placed)}")
    log(f"⏱ Интервал: {POLL_SEC}s")
    log("")

    while True:
        try:
            main_cycle(api_key, secret_key, placed)
        except KeyboardInterrupt:
            log("Остановлено пользователем")
            return
        except Exception as e:
            log(f"❌ CYCLE ERROR: {e}")
        log(f"Sleep {POLL_SEC}s...")
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
