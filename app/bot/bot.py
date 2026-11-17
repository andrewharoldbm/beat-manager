"""Telegram bot: only talks to the owner (OWNER_CHAT_ID).

- /status : system summary
- /kill   : stop everything (PIN required)
- /restore: resume (PIN required)
- audio files from owner -> saved to beats inbox (caption may tag producers: "@conkretus @rsd")
Messages never contain clickable links.
"""
import os
import re
import time
import hashlib
import httpx
from app.common.db import connect, log_event, ensure_schema

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER = int(os.environ.get("OWNER_CHAT_ID", "0") or 0)
PIN_HASH = os.environ.get("KILL_PIN_SHA256", "")
INBOX = os.environ.get("BEATS_INBOX", "/data/beats/inbox")
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
ALLOWED_EXT = {".mp3", ".wav"}
MAX_BYTES = 50 * 1024 * 1024
c = httpx.Client(timeout=60)


def strip_links(text: str) -> str:
    return re.sub(r"(https?://\S+|www\.\S+|\b\S+\.(com|net|org|io|lt|me|co)\S*)", "(link in dashboard)", text)


def send(text: str):
    if TOKEN and OWNER:
        c.post(f"{API}/sendMessage", json={"chat_id": OWNER, "text": strip_links(text),
                                            "disable_web_page_preview": True})


def pin_ok(pin: str) -> bool:
    return bool(PIN_HASH) and hashlib.sha256(pin.strip().encode()).hexdigest() == PIN_HASH


def status(conn):
    a = conn.execute("SELECT count(*) n, count(last_scanned) s FROM artists").fetchone()
    m = conn.execute("SELECT count(*) n FROM yt_metrics WHERE measured > now()-interval '24 hours'").fetchone()
    b = conn.execute("SELECT count(*) n FROM beats").fetchone()
    k = conn.execute("SELECT value FROM settings WHERE key='killed'").fetchone()["value"]
    return (f"Beat Manager status\n"
            f"State: {'STOPPED (kill)' if k is True else 'running'}\n"
            f"Artists in DB: {a['n']} (scanned: {a['s']})\n"
            f"Measurements last 24h: {m['n']}\n"
            f"Beats: {b['n']}")


def handle_audio(conn, msg):
    doc = msg.get("audio") or msg.get("document")
    if not doc:
        return
    fname = doc.get("file_name") or f"beat_{doc['file_unique_id']}.mp3"
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_EXT or doc.get("file_size", 0) > MAX_BYTES:
        send("Ignored: only .mp3/.wav up to 50MB are accepted.")
        return
    info = c.get(f"{API}/getFile", params={"file_id": doc["file_id"]}).json()
    path = info.get("result", {}).get("file_path")
    if not path:
        send("Could not fetch file (Telegram bot limit is 20MB per download).")
        return
    data = c.get(f"{FILE_API}/{path}").content
    # basic magic-byte check
    if not (data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or data[:4] == b"RIFF"):
        send("Ignored: file is not a valid audio file.")
        return
    os.makedirs(INBOX, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9 _.@$-]", "_", fname)
    dest = os.path.join(INBOX, f"{int(time.time())}_{safe}")
    with open(dest, "wb") as f:
        f.write(data)
    producers = re.findall(r"@([A-Za-z0-9_$-]+)", (msg.get("caption") or "") + " " + fname) or ["conkretus"]
    caption = msg.get("caption") or ""
    genres = re.findall(r"#([A-Za-z0-9_-]+)", caption)
    conn.execute("INSERT INTO beats(file_path, original_name, producers, caption, genre) VALUES (%s,%s,%s,%s,%s)",
                 (dest, fname, producers, caption, genres[0].lower() if genres else None))
    send(f"Beat received: {fname}\nProducers: {' x '.join(producers)}\nGenre: {genres[0] if genres else 'auto'}\nIt will be analyzed shortly.")


def run():
    if not TOKEN or not OWNER:
        print("Telegram not configured; bot idle")
        while True:
            time.sleep(3600)
    conn = connect()
    ensure_schema(conn)
    send("Beat Manager is online.")
    offset = 0
    pending = None  # ('kill'|'restore')
    while True:
        try:
            r = c.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 50}, timeout=60).json()
        except Exception:
            time.sleep(5)
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if msg.get("chat", {}).get("id") != OWNER:
                continue  # ignore everyone else
            text = (msg.get("text") or "").strip()
            if pending and text and not text.startswith("/"):
                if pin_ok(text):
                    killed = pending == "kill"
                    conn.execute("UPDATE settings SET value=%s::jsonb WHERE key='killed'", ("true" if killed else "false",))
                    log_event(conn, "alert", pending, f"{pending} executed by owner")
                    send("KILL executed: all jobs stopped. Next steps:\n1. Change your Google passwords\n"
                         "2. Remove unknown devices in Google account security\n3. Revoke app access in Google account permissions\n"
                         "4. When safe, send /restore" if killed else "RESTORE done: system resumed.")
                else:
                    send("Wrong PIN.")
                pending = None
                continue
            if text == "/status":
                send(status(conn))
            elif text == "/queue":
                rows = conn.execute("""SELECT u.scheduled, coalesce(u.title, b.name) t, u.status FROM uploads u
                                       JOIN beats b ON b.id=u.beat_id WHERE u.status IN ('scheduled','final')
                                       ORDER BY u.scheduled LIMIT 7""").fetchall()
                waiting = conn.execute("SELECT count(*) n FROM beats WHERE status='analyzed'").fetchone()["n"]
                lines = [f"{r['scheduled']:%m-%d %H:%M} UTC  {r['t']}" + (" (preliminary)" if r['status']=='scheduled' else "") for r in rows]
                send("Upcoming uploads:\n" + ("\n".join(lines) or "none") + f"\nAnalyzed, waiting for YouTube to be enabled: {waiting}")
            elif text == "/review":
                rows = conn.execute("SELECT name, review_note FROM beats WHERE needs_review ORDER BY id DESC LIMIT 10").fetchall()
                send("Needs review:\n" + ("\n".join(f"{r['name']}: {r['review_note']}" for r in rows) or "nothing"))
            elif text == "/kill":
                pending = "kill"
                send("Send your PIN to confirm KILL.")
            elif text == "/restore":
                pending = "restore"
                send("Send your PIN to confirm RESTORE.")
            elif msg.get("audio") or msg.get("document"):
                handle_audio(conn, msg)


if __name__ == "__main__":
    run()
