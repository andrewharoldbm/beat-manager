"""Weekly beat packs to contacts + reply/bounce tracking. OFF unless settings.email_enabled.

- builds a pack once a week (email_weekday): 4-5 mp3s, total attachment size kept under ~18MB
  (Gmail limit 25MB incl. encoding); WAVs converted to 192k mp3
- sends to valid, not-unsubscribed contacts, spread over days with email_daily_limit
- reads replies: "stop/unsubscribe" -> unsubscribed; other replies -> owner notified on Telegram
- bounces (mailer-daemon) -> contact marked bounced
"""
import datetime as dt
import os
import re
import secrets
import subprocess
import time
import traceback
import httpx
from app.common.db import connect, ensure_schema, is_killed, log_event
from app.common import settings as S
from app.email import gmail, contacts

PACK_DIR = os.environ.get("PACK_DIR", "/data/renders/packs")
MAX_BYTES = 18 * 1024 * 1024
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER = os.environ.get("OWNER_CHAT_ID", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://155-133-197-189.sslip.io")


def notify(text):
    if TOKEN and OWNER:
        try:
            httpx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                       json={"chat_id": int(OWNER), "text": text, "disable_web_page_preview": True}, timeout=20)
        except Exception:
            pass


def to_mp3(src, beat_id):
    os.makedirs(PACK_DIR, exist_ok=True)
    out = os.path.join(PACK_DIR, f"beat_{beat_id}.mp3")
    if not os.path.exists(out):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-codec:a", "libmp3lame", "-b:a", "192k", out],
                       check=True, timeout=600)
    return out


def build_pack(conn, size):
    beats = conn.execute("""SELECT * FROM beats WHERE status IN ('analyzed','queued','uploaded')
                            AND id NOT IN (SELECT unnest(beat_ids) FROM email_packs WHERE beat_ids IS NOT NULL)
                            ORDER BY (status='uploaded') DESC, id DESC LIMIT %s""", (size + 3,)).fetchall()
    chosen, total = [], 0
    for b in beats:
        mp3 = to_mp3(b["file_path"], b["id"])
        s = os.path.getsize(mp3)
        if total + s > MAX_BYTES:
            continue
        chosen.append((b, mp3))
        total += s
        if len(chosen) >= size:
            break
    if not chosen:
        return None
    row = conn.execute("INSERT INTO email_packs(beat_ids) VALUES (%s) RETURNING id",
                       ([b["id"] for b, _ in chosen],)).fetchone()
    return row["id"]


def pack_files(conn, pack_id):
    p = conn.execute("SELECT beat_ids FROM email_packs WHERE id=%s", (pack_id,)).fetchone()
    rows = conn.execute("SELECT * FROM beats WHERE id = ANY(%s)", (p["beat_ids"],)).fetchall()
    out = []
    for b in rows:
        prods = " x ".join(b["producers"] or ["conkretus"])
        name = f"{b['name'] or 'BEAT'} {int(b['bpm'] or 0)}bpm {b['musical_key'] or ''} prod {prods}".strip()
        out.append((b, to_mp3(b["file_path"], b["id"]), re.sub(r"[^A-Za-z0-9 #-]", "", name)[:80] + ".mp3"))
    return out


def compose(files, contact, track_token):
    first = (contact["name"] or "").split(" ")[0]
    lines = [f"yo{(' ' + first) if first else ''},", "",
             f"sending you {len(files)} new beats, mp3s attached so you can play them right here:", ""]
    for b, _, _ in files:
        lines.append(f"- {b['name']}  |  {int(b['bpm'] or 0)} BPM  |  {b['musical_key'] or ''}  |  prod. {' x '.join(b['producers'] or ['conkretus'])}")
    lines += ["", "if anything fits, reply and we'll lock it in.", "", "conkretus", "",
              "(reply \"stop\" and I won't send more)"]
    text = "\n".join(lines)
    html = "<br>".join(l.replace("<", "&lt;") for l in lines) + \
        f'<img src="{PUBLIC_URL}/o/{track_token}.gif" width="1" height="1" alt="">'
    return text, html


def send_due(conn, svc):
    if dt.datetime.now(dt.timezone.utc).weekday() == int(S.get(conn, "email_weekday")):
        last = conn.execute("SELECT max(sent_at) t FROM email_packs WHERE sent_at IS NOT NULL").fetchone()["t"]
        if not last or last < dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=6):
            pid = build_pack(conn, int(S.get(conn, "email_pack_size")))
            if pid:
                conn.execute("UPDATE email_packs SET sent_at=now() WHERE id=%s", (pid,))
                notify(f"New email pack #{pid} created; sending to contacts over the next days.")
    pack = conn.execute("SELECT id FROM email_packs WHERE sent_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if not pack:
        return
    sent_today = conn.execute("SELECT count(*) n FROM email_log WHERE sent_at > now()-interval '24 hours'").fetchone()["n"]
    budget = int(S.get(conn, "email_daily_limit")) - sent_today
    if budget <= 0:
        return
    targets = conn.execute("""SELECT * FROM contacts WHERE status='valid' AND NOT unsubscribed
                              AND id NOT IN (SELECT contact_id FROM email_log WHERE pack_id=%s)
                              AND (last_sent IS NULL OR last_sent < now()-interval '6 days')
                              ORDER BY replied DESC, opened_count DESC, id LIMIT %s""", (pack["id"], min(budget, 5))).fetchall()
    if not targets:
        return
    files = pack_files(conn, pack["id"])
    for c in targets:
        tok = secrets.token_urlsafe(12)
        text, html = compose(files, c, tok)
        try:
            r = gmail.send(svc, c["email"], "new beats for you", text, html, [(f, n) for _, f, n in files])
            conn.execute("INSERT INTO email_log(contact_id, pack_id, gmail_id, thread_id, status) VALUES (%s,%s,%s,%s,%s)",
                         (c["id"], pack["id"], r.get("id"), r.get("threadId"), "sent:" + tok))
            conn.execute("UPDATE contacts SET last_sent=now(), sent_count=sent_count+1 WHERE id=%s", (c["id"],))
        except Exception as e:
            log_event(conn, "warn", "email", f"send to {c['email']} failed: {e}")
        time.sleep(30)  # slow and steady


def read_replies(conn, svc):
    res = svc.users().messages().list(userId="me", q="newer_than:3d -from:me", maxResults=50).execute()
    for m in res.get("messages", []):
        full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                          metadataHeaders=["From", "Subject"]).execute()
        hdr = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
        frm = hdr.get("From", "").lower()
        snippet = (full.get("snippet") or "").lower()
        log = conn.execute("SELECT e.id, e.contact_id, c.email FROM email_log e JOIN contacts c ON c.id=e.contact_id WHERE e.thread_id=%s",
                           (full.get("threadId"),)).fetchone()
        if "mailer-daemon" in frm or "postmaster" in frm:
            for em in contacts.EMAIL_RE.findall(snippet):
                conn.execute("UPDATE contacts SET status='bounced' WHERE email=%s", (em.lower(),))
            continue
        if not log:
            continue
        if re.search(r"\b(stop|unsubscribe|remove me)\b", snippet):
            conn.execute("UPDATE contacts SET unsubscribed=true WHERE id=%s", (log["contact_id"],))
        else:
            already = conn.execute("SELECT replied FROM contacts WHERE id=%s", (log["contact_id"],)).fetchone()["replied"]
            if not already:
                conn.execute("UPDATE contacts SET replied=true, status='valid' WHERE id=%s", (log["contact_id"],))
                notify(f"Reply from {log['email']}: \"{(full.get('snippet') or '')[:200]}\"\nOpen your Gmail to answer. Don't open attachments from unknown senders.")


def run():
    conn = connect()
    ensure_schema(conn)
    log_event(conn, "info", "mailer", "mailer started")
    while True:
        try:
            contacts.verify_pending(conn, 100)
            if S.get(conn, "email_enabled") and not is_killed(conn):
                svc = gmail.service()
                if svc:
                    send_due(conn, svc)
                    read_replies(conn, svc)
        except Exception:
            log_event(conn, "warn", "mailer", traceback.format_exc()[-500:])
        time.sleep(900)


if __name__ == "__main__":
    run()
