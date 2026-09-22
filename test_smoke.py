"""Smoke tests — базовая проверка что код не сломан."""
import json, sys
from pathlib import Path

ROOT = Path("/Users/andromeda/crypto-carry")
passed = 0
failed = 0

def check(name, cond):
    global passed, failed
    if cond:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name}")
        failed += 1

print("=" * 60)
print("  SMOKE TESTS")
print("=" * 60)

# Файлы существуют
check("live_collector.py", (ROOT/"live_collector.py").exists())
check("place_order_real.py", (ROOT/"place_order_real.py").exists())
check("update_trailing_stops.py", (ROOT/"update_trailing_stops.py").exists())

# Ключи
env_real = ROOT / ".env.real"
if env_real.exists():
    content = env_real.read_text()
    check(".env.real: API_KEY есть", "BINANCE_REAL_API_KEY=" in content)
    check(".env.real: SECRET есть", "BINANCE_REAL_SECRET_KEY=" in content)
    check(".env.real: chmod 600", oct(env_real.stat().st_mode)[-3:] == "600")

# State файлы
state = ROOT / "live_state.json"
if state.exists():
    s = json.loads(state.read_text())
    check("live_state: есть positions", "positions" in s)
    check("live_state: есть signals", "signals" in s)

# Синтаксис
import ast
for f in ["live_collector.py", "place_order_real.py", "update_trailing_stops.py"]:
    path = ROOT/f
    if path.exists():
        try:
            ast.parse(path.read_text())
            check(f"{f}: syntax OK", True)
        except SyntaxError as e:
            check(f"{f}: syntax", False)

# place_order_real содержит новый endpoint
por = (ROOT/"place_order_real.py").read_text()
check("algoOrder endpoint", "/fapi/v1/algoOrder" in por)
check("get_open_positions", "def get_open_positions" in por)
check("MAX_NOTIONAL", "NOTIONAL_PER_TRADE" in por)

# gitignore
gi = (ROOT/".gitignore")
if gi.exists():
    gcontent = gi.read_text()
    check(".gitignore: .env.real", ".env.real" in gcontent)
    check(".gitignore: .venv", ".venv/" in gcontent)

print()
print("=" * 60)
print(f"  PASSED: {passed}  |  FAILED: {failed}")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
