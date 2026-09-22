import time, hmac, hashlib, requests
from pathlib import Path
from urllib.parse import urlencode
env = {}
for line in Path(".env.real").read_text().splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
ak, sk = env["BINANCE_REAL_API_KEY"], env["BINANCE_REAL_SECRET_KEY"]
def signed(method, path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time()*1000)
    params["recvWindow"] = 5000
    q = urlencode(params)
    sig = hmac.new(sk.encode(), q.encode(), hashlib.sha256).hexdigest()
    url = f"https://fapi.binance.com{path}?{q}&signature={sig}"
    h = {"X-MBX-APIKEY": ak}
    if method == "GET": return requests.get(url, headers=h, timeout=10)
    if method == "POST": return requests.post(url, headers=h, timeout=10)
    if method == "DELETE": return requests.delete(url, headers=h, timeout=10)

# 1. Отменяем старый XRP TRAILING (0.5%)
r = signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": "XRPUSDT"})
for o in r.json():
    if o["orderType"] == "TRAILING_STOP_MARKET":
        r2 = signed("DELETE", "/fapi/v1/algoOrder", {"algoId": o["algoId"]})
        print(f"Отменён старый XRP TRAILING: {r2.status_code}")
        time.sleep(0.5)

# 2. Ставим новый (0.8%)
r = signed("GET", "/fapi/v2/positionRisk", {"symbol": "XRPUSDT"})
pos = [p for p in r.json() if abs(float(p["positionAmt"])) > 0][0]
qty = abs(float(pos["positionAmt"]))
r = signed("POST", "/fapi/v1/algoOrder", {
    "algoType": "CONDITIONAL", "symbol": "XRPUSDT", "side": "SELL",
    "type": "TRAILING_STOP_MARKET", "callbackRate": "0.8",
    "quantity": qty, "reduceOnly": "true", "workingType": "CONTRACT_PRICE",
})
print(f"Новый TRAILING создан: {r.status_code}")

# Финальная проверка
r = signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": "XRPUSDT"})
for o in r.json():
    print(f"  {o['symbol']} {o['orderType']} callback={o.get('callbackRate','-')}")
