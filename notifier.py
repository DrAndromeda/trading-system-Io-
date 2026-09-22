"""Telegram notifier для Binance ордеров."""
import os, json, time, requests
from pathlib import Path

ROOT = Path("/Users/andromeda/crypto-carry")
ENV = ROOT / ".env.telegram"

def load_tg():
    env = {}
    for line in ENV.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env.get("TELEGRAM_TOKEN"), env.get("TELEGRAM_CHAT_ID")

def send(text):
    token, chat = load_tg()
    if not token or "СЮДА" in token:
        print("⚠️ Telegram не настроен — .env.telegram не заполнен")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        print(f"TG err: {e}")
        return False

if __name__ == "__main__":
    ok = send("🧪 <b>Тест</b>\nNotifier работает!")
    print("✅ Отправлено" if ok else "❌ Не отправлено")
