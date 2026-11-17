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
import random
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


def compose(conn, files, contact, track_token):
    lines = [S.get(conn, "email_body"), "", S.get(conn, "email_signature")]
    text = "\n".join(lines)
    html = "<br>".join(l.replace("<", "&lt;") for l in text.split("\n")) + \
        f'<img src="{PUBLIC_URL}/o/{track_token}.gif" width="1" height="1" alt="">'
    return text, html


RESEND_MIN_DAYS = 14


def resend_targets(conn, limit):
    """People who got a pack 14+ days ago, never opened it, never replied, and haven't had it re-sent.
    Oldest/weakest packs first. Each person gets each pack at most twice in total."""
    return conn.execute("""
        SELECT c.*, l.pack_id FROM email_log l JOIN contacts c ON c.id=l.contact_id
        WHERE l.kind='first' AND l.status NOT LIKE 'opened%%' AND l.sent_at < now() - make_interval(days => %s)
          AND c.status='valid' AND NOT c.unsubscribed AND NOT c.replied AND """ + audience_sql(conn, "c") + """
          AND (c.last_sent IS NULL OR c.last_sent < now()-interval '6 days')
          AND NOT EXISTS (SELECT 1 FROM email_log r WHERE r.contact_id=c.id AND r.pack_id=l.pack_id AND r.kind='resend')
        ORDER BY l.pack_id, c.id LIMIT %s""", (RESEND_MIN_DAYS, limit)).fetchall()


def daily_limit(conn):
    """Warm-up: start at 30/day on the first sending day, +15 per day, up to the target.
    Target 360/day reaches ~2500 contacts per week."""
    target = int(S.get(conn, "email_daily_limit"))
    first = conn.execute("SELECT min(sent_at) t FROM email_log").fetchone()["t"]
    if not first:
        return min(target, 30)
    days = (dt.datetime.now(dt.timezone.utc) - first).days
    # slow down if many bounces lately
    b = conn.execute("""SELECT count(*) FILTER (WHERE c.status='bounced') bad, count(*) n FROM email_log l
                        JOIN contacts c ON c.id=l.contact_id WHERE l.sent_at > now()-interval '3 days'""").fetchone()
    lim = min(target, 30 + 15 * days)
    if b["n"] >= 50 and b["bad"] / b["n"] > 0.05:
        lim = max(20, lim // 2)
    return lim


def audience_sql(conn, alias=""):
    a = alias + "." if alias else ""
    parts = []
    if S.get(conn, "email_send_own"):
        parts.append(f"coalesce({a}category,'own') = 'own'")
    if S.get(conn, "email_send_found"):
        parts.append(f"{a}category = 'artist'")
    return "(" + (" OR ".join(parts) or "false") + ")"


def send_due(conn, svc):
    now = dt.datetime.now(dt.timezone.utc)
    if now.weekday() == int(S.get(conn, "email_weekday")):
        last = conn.execute("SELECT max(sent_at) t FROM email_packs").fetchone()["t"]
        if not last or last < now - dt.timedelta(days=6):
            pid = build_pack(conn, int(S.get(conn, "email_pack_size")))
            if pid:
                conn.execute("UPDATE email_packs SET sent_at=now() WHERE id=%s", (pid,))
                notify(f"New email pack #{pid} created; sending to contacts over the next days.")
            elif not conn.execute("SELECT 1 FROM events WHERE kind='email' AND message='no new beats' AND at > now()-interval '6 days'").fetchone():
                log_event(conn, "info", "email", "no new beats")
                notify("No new beats left for email packs. Re-sending older packs to people who didn't open them. Send more beats when you can.")
    sent_today = conn.execute("SELECT count(*) n FROM email_log WHERE sent_at > now()-interval '24 hours'").fetchone()["n"]
    limit = daily_limit(conn)
    budget = min(limit - sent_today, max(2, -(-limit // 80)))  # spread over the day (runs every 15 min)
    if budget <= 0:
        return
    jobs = []  # (contact, pack_id, kind)
    pack = conn.execute("SELECT id FROM email_packs WHERE sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 1").fetchone()
    if pack:
        for c in conn.execute("""SELECT * FROM contacts WHERE status='valid' AND NOT unsubscribed AND """ + audience_sql(conn) + """
                                 AND id NOT IN (SELECT contact_id FROM email_log WHERE pack_id=%s)
                                 AND (last_sent IS NULL OR last_sent < now()-interval '6 days')
                                 ORDER BY replied DESC, opened_count DESC, id LIMIT %s""", (pack["id"], budget)).fetchall():
            jobs.append((c, pack["id"], "first"))
    if len(jobs) < budget:  # everyone has the newest pack -> use spare capacity on re-sends
        seen = {c["id"] for c, _, _ in jobs}
        for c in resend_targets(conn, budget - len(jobs)):
            if c["id"] not in seen:
                jobs.append((c, c["pack_id"], "resend"))
    files_cache = {}
    for c, pid, kind in jobs:
        files = files_cache.setdefault(pid, pack_files(conn, pid))
        tok = secrets.token_urlsafe(12)
        text, html = compose(conn, files, c, tok)
        subject = pick_subject(conn, c["id"])
        try:
            r = gmail.send(svc, c["email"], subject, text, html, [(f, n) for _, f, n in files])
            conn.execute("INSERT INTO email_log(contact_id, pack_id, gmail_id, thread_id, status, kind, subject) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (c["id"], pid, r.get("id"), r.get("threadId"), "sent:" + tok, kind, subject))
            conn.execute("UPDATE contacts SET last_sent=now(), sent_count=sent_count+1 WHERE id=%s", (c["id"],))
        except Exception as e:
            log_event(conn, "warn", "email", f"send to {c['email']} failed: {e}")
        time.sleep(30)  # slow and steady


SUBJECT_TWISTS = ["", " this week", " for you", " rn", " (new)", " pt 2", " pt 3"]


def pick_subject(conn, contact_id):
    """Never repeat a subject to the same person; prefer ones used least lately overall."""
    base = S.get(conn, "email_subjects") or ["fresh beats"]
    pool = [b + t for t in SUBJECT_TWISTS for b in base]
    used = {r["subject"] for r in conn.execute(
        "SELECT subject FROM email_log WHERE contact_id=%s AND subject IS NOT NULL", (contact_id,)).fetchall()}
    fresh = [x for x in pool if x not in used] or pool
    recent = {r["subject"]: r["n"] for r in conn.execute(
        """SELECT subject, count(*) n FROM email_log WHERE sent_at > now()-interval '7 days'
           AND subject IS NOT NULL GROUP BY subject""").fetchall()}
    low = min(recent.get(x, 0) for x in fresh)
    return random.choice([x for x in fresh if recent.get(x, 0) == low])


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
