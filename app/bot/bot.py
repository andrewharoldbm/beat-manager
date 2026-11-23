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
from app.common import producers as P
from app.common.genres import CANON
CANON_TAGS = list(CANON) + ['ambient', 'chill', 'dark', 'sad', 'hard', 'dreamy', 'melodic', 'emotional', 'energetic']

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
        return c.post(f"{API}/sendMessage", json={"chat_id": OWNER, "text": strip_links(text),
                                            "disable_web_page_preview": True})


def revoke_google_tokens():
    """KILL: revoke Google OAuth tokens and move token files aside so nothing can act on the accounts."""
    import json, glob
    done = []
    for path in glob.glob("/data/secrets/*_token.json"):
        try:
            tok = json.load(open(path))
            for t in (tok.get("refresh_token"), tok.get("token")):
                if t:
                    c.post("https://oauth2.googleapis.com/revoke", params={"token": t}, timeout=20)
            os.replace(path, path + ".revoked")
            done.append(os.path.basename(path))
        except Exception:
            pass
    return done


def pin_ok(pin: str) -> bool:
    return bool(PIN_HASH) and hashlib.sha256(pin.strip().encode()).hexdigest() == PIN_HASH


def status(conn):
    a = conn.execute("SELECT count(*) n, count(last_scanned) s FROM artists").fetchone()
    m = conn.execute("SELECT count(*) n FROM yt_metrics WHERE measured > now()-interval '24 hours'").fetchone()
    b = conn.execute("SELECT count(*) n FROM beats").fetchone()
    k = conn.execute("SELECT value FROM settings WHERE key='killed'").fetchone()["value"]
    e = conn.execute("""SELECT
        count(*) FILTER (WHERE category='artist') art, count(*) FILTER (WHERE category='artist' AND status='valid') art_ok,
        count(*) FILTER (WHERE category='artist' AND added > now()-interval '24 hours') art_new,
        count(*) FILTER (WHERE category='producer') prod, count(*) FILTER (WHERE category='producer' AND status='valid') prod_ok,
        count(*) FILTER (WHERE category='producer' AND added > now()-interval '24 hours') prod_new,
        count(*) FILTER (WHERE coalesce(category,'own')='own') own FROM contacts""").fetchone()
    pr = conn.execute("SELECT count(*) n, count(email_checked) c FROM producers").fetchone()
    ac = conn.execute("SELECT count(email_checked) n FROM artists").fetchone()["n"]
    return (f"Beat Manager status\n"
            f"State: {'STOPPED (kill)' if k is True else 'running'}\n"
            f"Artists in DB: {a['n']} (scanned: {a['s']})\n"
            f"Measurements last 24h: {m['n']}\n"
            f"Beats: {b['n']}\n\n\n"
            f"Artist emails\n"
            f"Collected: {e['art']} (verified: {e['art_ok']})\n"
            f"New last 24h: {e['art_new']}\n"
            f"Artists checked: {ac}\n\n\n"
            f"Producer emails\n"
            f"Collected: {e['prod']} (verified: {e['prod_ok']})\n"
            f"New last 24h: {e['prod_new']}\n"
            f"Producers found: {pr['n']} (checked: {pr['c']})\n\n\n"
            f"My list: {e['own']}")


def handle_cover(conn, msg):
    """Photo sent as a reply to a beat (or to my 'Got it' message) becomes that beat's cover."""
    b = beat_for_reply(conn, msg)
    if not b:
        send("To set a cover, reply to the beat (or to my 'Got it' message) with the picture.")
        return
    f = (msg.get("photo") or [None])[-1] or msg.get("document")
    info = c.get(f"{API}/getFile", params={"file_id": f["file_id"]}).json()
    path = info.get("result", {}).get("file_path")
    if not path:
        send("Could not fetch the picture.")
        return
    try:
        from app.pipeline import custom_cover
        custom_cover.save(conn, b["id"], c.get(f"{FILE_API}/{path}").content)
        send(f"Cover set for {b['name'] or b['original_name']}.")
    except Exception as e:
        send(f"Cover not accepted: {e}")


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
    producers = P.parse((msg.get("caption") or "") + " " + fname, conn)
    caption = msg.get("caption") or ""
    genres = re.findall(r"#([A-Za-z0-9_-]+)", caption)
    bid = conn.execute("""INSERT INTO beats(file_path, original_name, producers, caption, genre, tg_msg_ids)
                          VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                       (dest, fname, producers, caption, genres[0].lower() if genres else None,
                        [msg["message_id"]])).fetchone()["id"]
    r = send(f"Got it: {fname}\nThis beat is prod. by {' x '.join(producers)}\nGenre: {genres[0] if genres else 'not set (I will ask)'}\n"
             "Wrong producers? Reply to this message with @name to add or -@name to remove.")
    remember_msg(conn, bid, r)


def remember_msg(conn, beat_id, resp):
    try:
        mid = resp.json()["result"]["message_id"]
        conn.execute("UPDATE beats SET tg_msg_ids = array_append(coalesce(tg_msg_ids,'{}'), %s) WHERE id=%s", (mid, beat_id))
    except Exception:
        pass


PROD_LINE = re.compile(r"prod\. by [^\n]+")


def change_producers(conn, beat, names):
    """@name adds a producer, -@name removes one. conkretus always stays."""
    cur = list(beat["producers"] or ["conkretus"])
    for n in names:
        if n.startswith("-"):
            n = P.canon(n[1:], conn)
            if n.lower() != "conkretus":
                cur = [p for p in cur if p.lower() != n.lower()]
        else:
            n = P.canon(n, conn)
            if n.lower() not in [p.lower() for p in cur]:
                cur.append(n)
    if "conkretus" not in [p.lower() for p in cur]:
        cur.insert(0, "conkretus")
    conn.execute("UPDATE beats SET producers=%s WHERE id=%s", (cur, beat["id"]))
    prods = " x ".join(cur)
    note = "the YouTube description and emails will use this"
    for u in conn.execute("SELECT * FROM uploads WHERE beat_id=%s", (beat["id"],)).fetchall():
        if u.get("description"):
            conn.execute("UPDATE uploads SET description=%s WHERE id=%s",
                         (PROD_LINE.sub(f"prod. by {prods}", u["description"]), u["id"]))
        if u["platform"] == "youtube" and u["status"] == "uploaded" and u["external_id"]:
            try:
                from app.pipeline import youtube_api
                youtube_api.update_description(u["external_id"], lambda d: PROD_LINE.sub(f"prod. by {prods}", d))
                note = "YouTube description updated too"
            except Exception as e:
                log_event(conn, "warn", "bot", f"yt description update failed: {e}")
                note = "could NOT update the YouTube description, fix it by hand"
    send(f"OK, {beat['name'] or beat['original_name']} is now: prod. by {prods}\n({note})")


REAL = "(a.keep OR a.verified OR a.discovered_via = 'seed')"
LAST = """JOIN LATERAL (SELECT * FROM yt_metrics y WHERE y.artist_id=a.id ORDER BY measured DESC LIMIT 1) m ON true"""
OK = f"m.rel_n >= 5 AND m.vpd_med >= 40 AND {REAL} AND (a.keep OR a.hidden IS NULL) AND NOT (m.vpd_med >= 800 AND m.sup30 <= 2)"


def _line(i, r):
    s7, s30 = r["sup7"] or 0, r["sup30"] or 0
    trend = " ↑" if s30 >= 4 and s7 * 4.3 > s30 * 1.4 else ""
    return f"{i}. {r['name']} - {r['vpd_med']:.0f} views/day | {s30} beats/30d ({s7} last 7d){trend}"


def top_niches(conn, q, n=20):
    """/top <genre>: biggest + best niche artists of that genre.
    /top <artist>: artists that fit next to / instead of that artist, then the best underground niches among them."""
    from app.common.db import norm_key
    from app.common.generic_names import is_junk
    art = conn.execute(f"""SELECT a.* FROM artists a WHERE a.key=%s AND {REAL}""", (norm_key(q),)).fetchone() if q else None
    if q and not art and q not in CANON_TAGS:  # "rio da yung" -> Rio Da Yung OG
        art = conn.execute(f"""SELECT a.* FROM artists a {LAST} WHERE a.key LIKE %s AND {REAL}
                               ORDER BY m.vpd_med DESC LIMIT 1""", (norm_key(q) + "%",)).fetchone()
    if art:
        rows = conn.execute(f"""
            WITH rel AS (
              SELECT b AS id, max(weight) w FROM artist_links WHERE a=%(id)s GROUP BY b
              UNION ALL SELECT a AS id, max(weight) w FROM artist_links WHERE b=%(id)s GROUP BY a
              UNION ALL SELECT t2.artist_id, sum(least(t1.weight, t2.weight)) * 0.5
                        FROM artist_tags t1 JOIN artist_tags t2 ON t2.tag=t1.tag AND t2.artist_id<>t1.artist_id
                        WHERE t1.artist_id=%(id)s AND t1.tag = ANY(%(canon)s) AND t1.weight >= 0.3 AND t2.weight >= 0.3
                        GROUP BY t2.artist_id)
            SELECT a.name, a.lastfm_listeners lis, m.vpd_med, m.sup30, m.sup7, sum(rel.w) fit FROM rel JOIN artists a ON a.id=rel.id {LAST}
            WHERE {OK} AND a.id <> %(id)s GROUP BY a.name, a.lastfm_listeners, m.vpd_med, m.sup30, m.sup7
            ORDER BY sum(rel.w) DESC LIMIT 300""", {"id": art["id"], "canon": CANON_TAGS}).fetchall()
        rows = [r for r in rows if not is_junk(r["name"])]
        fit = sorted(rows[:60], key=lambda r: -r["vpd_med"])[:n]
        niche = sorted([r for r in rows if r["sup30"] <= 10 and (r["lis"] or 0) < 1_500_000], key=lambda r: -(r["vpd_med"] / (r["sup30"] + 2)))[:n]
        head = f"{art['name']}: artists that fit with / instead of him"
    else:
        rows = conn.execute(f"""
            SELECT a.name, a.lastfm_listeners lis, m.vpd_med, m.sup30, m.sup7 FROM artists a {LAST}
            WHERE {OK} AND (%(q)s = '' OR EXISTS (SELECT 1 FROM artist_tags t WHERE t.artist_id=a.id
                  AND t.tag = %(q)s AND t.weight >= 0.3))
            ORDER BY m.vpd_med DESC LIMIT 400""", {"q": q}).fetchall()
        rows = [r for r in rows if not is_junk(r["name"])]
        fit = rows[:10]
        niche = sorted([r for r in rows if r["sup30"] <= 10 and (r["lis"] or 0) < 1_500_000], key=lambda r: -(r["vpd_med"] / (r["sup30"] + 2)))[:n]
        head = f"{q or 'all genres'}: biggest artists"
    if not rows:
        return (f"Nothing measured for '{q}' yet. Use one artist name (e.g. /top rio da yung og) "
                "or one genre (e.g. /top detroit, /top ambient).")
    out = [head] + [_line(i, r) for i, r in enumerate(fit, 1)]
    out += ["", f"TOP {len(niche)} UNDERGROUND NICHES (searched, few type beats):"]
    out += [_line(i, r) for i, r in enumerate(niche, 1)] or ["none yet - the database is still growing"]
    out += ["", "↑ = more producers jumping on this week"]
    return "\n".join(out)


def beat_for_reply(conn, msg):
    rt = msg.get("reply_to_message")
    if not rt:
        return None
    return conn.execute("SELECT * FROM beats WHERE %s = ANY(tg_msg_ids) ORDER BY id DESC LIMIT 1",
                        (rt["message_id"],)).fetchone()


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
            r = c.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 50,
                                                   "allowed_updates": '["message","callback_query"]'}, timeout=60).json()
        except Exception:
            time.sleep(5)
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if cq:
                if cq.get("from", {}).get("id") == OWNER:
                    pm = re.fullmatch(r"prop:(\d+):(yes|no)", cq.get("data") or "")
                    if pm:
                        p = conn.execute("SELECT * FROM proposals WHERE id=%s AND status='open'", (int(pm.group(1)),)).fetchone()
                        if p and pm.group(2) == "yes":
                            from app.maint.worker import apply_proposal
                            apply_proposal(conn, p)
                        elif p:
                            conn.execute("UPDATE proposals SET status='rejected', decided=now() WHERE id=%s", (p["id"],))
                        c.post(f"{API}/answerCallbackQuery", json={"callback_query_id": cq["id"], "text": "Done" if p else "Already decided"})
                        continue
                    m = re.fullmatch(r"genre:(\d+):([a-z_-]{2,20})", cq.get("data") or "")
                    if m:
                        conn.execute("UPDATE beats SET genre=%s WHERE id=%s", (m.group(2), int(m.group(1))))
                        c.post(f"{API}/answerCallbackQuery", json={"callback_query_id": cq["id"], "text": f"Genre set: {m.group(2)}"})
                continue
            msg = u.get("message") or {}
            if msg.get("chat", {}).get("id") != OWNER or msg.get("from", {}).get("id") != OWNER:
                fr = msg.get("from", {})
                log_event(conn, "warn", "bot", f"ignored message from id {fr.get('id')} @{fr.get('username')}")
                if not hasattr(run, "_warned"):
                    run._warned = set()
                if fr.get("id") not in run._warned:
                    run._warned.add(fr.get("id"))
                    send(f"Someone else wrote to the bot and was ignored: id {fr.get('id')}, @{fr.get('username')}. Nothing was accepted from them.")
                continue
            text = (msg.get("text") or "").strip()
            if pending and text and not text.startswith("/"):
                if pin_ok(text):
                    killed = pending == "kill"
                    conn.execute("UPDATE settings SET value=%s::jsonb WHERE key='killed'", ("true" if killed else "false",))
                    if killed:
                        revoked = revoke_google_tokens()
                        log_event(conn, "alert", "kill", f"revoked: {revoked}")
                    log_event(conn, "alert", pending, f"{pending} executed by owner")
                    send("KILL executed: all jobs stopped. Next steps:\n1. Change your Google passwords\n"
                         "2. Remove unknown devices in Google account security\n3. Revoke app access in Google account permissions\n"
                         "4. When safe, send /restore, then log in to YouTube/Gmail again on the server" if killed else "RESTORE done: system resumed. Google logins must be redone (auth_google.py).")
                else:
                    send("Wrong PIN.")
                pending = None
                continue
            names = re.findall(r"-?@[A-Za-z0-9_$-]+", text)
            names = [n if n.startswith("-") else n[1:] for n in names]
            if names and msg.get("reply_to_message"):
                b = beat_for_reply(conn, msg)
                if b:
                    change_producers(conn, b, names)
                else:
                    send("I don't know which beat that message belongs to. Reply to the beat or to my 'Beat received' message.")
                continue
            m = re.match(r"/prod\s+(.+?)\s+(@.+)$", text)
            if m:
                b = conn.execute("""SELECT * FROM beats WHERE lower(coalesce(name, original_name)) LIKE %s
                                    ORDER BY id DESC LIMIT 1""", (f"%{m.group(1).lower()}%",)).fetchone()
                if b:
                    change_producers(conn, b, re.findall(r"-?@[A-Za-z0-9_$-]+", m.group(2)))
                else:
                    send(f"No beat found matching '{m.group(1)}'.")
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
            elif text.startswith("/top"):
                send(top_niches(conn, text[4:].strip().lower()))
            elif text == "/log":
                rows = conn.execute("""SELECT at, level, kind, message FROM events WHERE level IN ('warn','alert')
                                       OR kind IN ('emails','producers') ORDER BY id DESC LIMIT 12""").fetchall()
                send("Recent log:\n" + ("\n".join(f"{r['at']:%m-%d %H:%M} {r['kind']}: {r['message'][:160]}" for r in rows) or "empty"))
            elif text == "/review":
                rows = conn.execute("SELECT name, review_note FROM beats WHERE needs_review ORDER BY id DESC LIMIT 10").fetchall()
                send("Needs review:\n" + ("\n".join(f"{r['name']}: {r['review_note']}" for r in rows) or "nothing"))
            elif text in ("/pause", "/resume"):
                from app.common import settings as S
                S.set_(conn, "yt_enabled", text == "/resume")
                send("YouTube uploading " + ("RESUMED." if text == "/resume" else "PAUSED. Beats keep being analyzed."))
            elif text == "/help":
                send("Commands:\n/status - system state\n/queue - next uploads\n/review - beats to check\n/log - recent problems\n/top <genre or name> - best artists, e.g. /top ambient\n"
                     "/pause, /resume - YouTube uploading\n/prod <beat name> @name / -@name - add or remove a producer (or just reply to the beat)\n/kill, /restore - emergency stop (PIN)\n"
                     "Send an mp3/wav with a caption like: #detroit @conkretus @rsd")
            elif text == "/kill":
                pending = "kill"
                send("Send your PIN to confirm KILL.")
            elif text == "/restore":
                pending = "restore"
                send("Send your PIN to confirm RESTORE.")
            elif msg.get("photo") or (msg.get("document") or {}).get("mime_type", "").startswith("image/"):
                handle_cover(conn, msg)
            elif msg.get("audio") or msg.get("document"):
                handle_audio(conn, msg)


if __name__ == "__main__":
    run()
