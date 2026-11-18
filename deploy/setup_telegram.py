"""Interactive Telegram setup: run on the server.
  docker compose run --rm -it bot python deploy/setup_telegram.py   (writes to /tmp/tg.env)
Asks for bot token and a PIN, detects your chat id when you send /start to the bot.
"""
import sys
sys.path.insert(0, "/srv")
import getpass
import hashlib
import time
import httpx

token = input("Paste the bot token from BotFather: ").strip()
me = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20).json()
if not me.get("ok"):
    raise SystemExit("Token invalid.")
print(f"Bot: @{me['result']['username']}. Now open Telegram, find this bot and send it: /start")
chat_id, offset = None, 0
while not chat_id:
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates",
                  params={"offset": offset, "timeout": 30}, timeout=40).json()
    for u in r.get("result", []):
        offset = u["update_id"] + 1
        m = u.get("message") or {}
        if (m.get("text") or "").startswith("/start"):
            chat_id = m["chat"]["id"]
    time.sleep(1)
print(f"Got your chat id: {chat_id}")
while True:
    p1 = getpass.getpass("Choose a PIN for KILL/RESTORE (min 6 digits): ")
    p2 = getpass.getpass("Repeat PIN: ")
    if p1 == p2 and len(p1) >= 6:
        break
    print("PINs don't match or too short.")
h = hashlib.sha256(p1.encode()).hexdigest()
with open("/tmp/tg.env", "w") as f:
    f.write(f"TELEGRAM_BOT_TOKEN={token}\nOWNER_CHAT_ID={chat_id}\nKILL_PIN_SHA256={h}\n")
print("Saved. Exit now; the next command will apply it.")
