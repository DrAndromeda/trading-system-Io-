"""LINK: STOP_MARKET → TRAILING_STOP_MARKET (callback 0.8%)"""
import time, hmac, hashlib, requests, json
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path("/Users/andromeda/crypto-carry")
ENV = ROOT / ".env.real"
SYMBOL = "LINKUSDT"
CALLBACK_RATE = "0.8"

env = {}
for line in ENV.read_text().splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

ak = env["BINANCE_REAL_API_KEY"]
sk = env["BINANCE_REAL_SECRET_KEY"]
BASE = "https://fapi.binance.com"

def signed(method, path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    q = urlencode(params)
    sig = hmac.new(sk.encode(), q.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{q}&signature={sig}"
    h = {"X-MBX-APIKEY": ak}
    if method == "GET": return requests.get(url, headers=h, timeout=10)
    if method == "POST": return requests.post(url, headers=h, timeout=10)
    if method == "DELETE": return requests.delete(url, headers=h, timeout=10)

print("=" * 60)
print("  LINK → TRAILING_STOP_MARKET")
print("=" * 60)

# 1. Проверяем позицию
r = signed("GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL})
pos = [p for p in r.json() if abs(float(p["positionAmt"])) > 0]
if not pos:
    print(f"❌ Нет открытой позиции {SYMBOL}"); exit(1)
pos = pos[0]
qty = abs(float(pos["positionAmt"]))
print(f"✅ Позиция {SYMBOL}: qty={qty} entry=${pos['entryPrice']}")

# 2. Ищем текущие algo ордера
r = signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": SYMBOL})
orders = r.json()
print(f"\nОткрытых algo ордеров: {len(orders)}")

# 3. Отменяем STOP_MARKET (не TRAILING и не TP)
to_cancel = []
for o in orders:
    ot = o.get("orderType", "")
    if ot == "STOP_MARKET":
        to_cancel.append(o["algoId"])
        print(f"  К отмене: algoId={o['algoId']} STOP_MARKET @ {o['triggerPrice']}")

for aid in to_cancel:
    r = signed("DELETE", "/fapi/v1/algoOrder", {"algoId": aid})
    if r.status_code == 200:
        print(f"  ✅ Отменён algoId={aid}")
    else:
        print(f"  ⚠️ Ошибка отмены {aid}: {r.text[:150]}")
    time.sleep(0.3)

# 4. Ставим TRAILING_STOP_MARKET
print(f"\nСтавим TRAILING_STOP_MARKET callback={CALLBACK_RATE}%...")
r = signed("POST", "/fapi/v1/algoOrder", {
    "algoType": "CONDITIONAL",
    "symbol": SYMBOL,
    "side": "SELL",
    "type": "TRAILING_STOP_MARKET",
    "callbackRate": CALLBACK_RATE,
    "quantity": qty,
    "reduceOnly": "true",
    "workingType": "CONTRACT_PRICE",
})
if r.status_code == 200:
    res = r.json()
    print(f"  ✅ TRAILING STOP создан: algoId={res.get('algoId')}")
else:
    print(f"  ❌ Ошибка: {r.text[:250]}")

# 5. Финальная проверка
print("\n" + "=" * 60)
print("  ФИНАЛЬНЫЕ ORDERS")
print("=" * 60)
r = signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": SYMBOL})
for o in r.json():
    print(f"  {o['symbol']} {o['orderType']} @ {o.get('triggerPrice') or o.get('callbackRate')} qty={o['quantity']}")
