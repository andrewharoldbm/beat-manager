"""Beat pipeline scheduler.

Every minute:
  1. analyze new beats (notify owner if something needs review)
  2. put analyzed beats into the upload calendar (UPLOADS_PER_DAY at UPLOAD_HOURS_UTC),
     avoiding the same genre back to back
  3. ~24h before an upload: choose the FINAL title/description/tags using the latest formulas
  4. at upload time: render video, upload to YouTube (if YT_ENABLED), post mp3 to Telegram channel
     (if TG_CHANNEL_ENABLED)
Everything stops while KILL is active. Uploading is OFF by default until the owner enables it.
"""
import datetime as dt
import os
import time
import traceback
import httpx
from app.common.db import connect, is_killed, log_event, ensure_schema
from app.pipeline import analyze as an, meta, render

from app.common import settings as S
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER = os.environ.get("OWNER_CHAT_ID", "")
FINALIZE_BEFORE = dt.timedelta(hours=24)



GENRE_BUTTONS = ["detroit", "flint", "scam", "memphis", "chicago", "rage", "plugg", "other"]


def notify(text, buttons=None):
    if TOKEN and OWNER:
        body = {"chat_id": int(OWNER), "text": text, "disable_web_page_preview": True}
        if buttons:
            body["reply_markup"] = {"inline_keyboard": buttons}
        try:
            httpx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=body, timeout=20)
        except Exception:
            pass


def now():
    return dt.datetime.now(dt.timezone.utc)


def step_analyze(conn):
    for b in conn.execute("SELECT * FROM beats WHERE status='new' ORDER BY id LIMIT 5").fetchall():
        try:
            r = an.analyze(b["file_path"], b["original_name"] or "", b["caption"] or "")
            genre_given = b["genre"] or (r["genres"][0] if r["genres"] else None)
            genre = genre_given or "detroit"
            conn.execute("""UPDATE beats SET bpm=%s, bpm_given=%s, musical_key=%s, key_alt=%s, tuning_hz=%s,
                            tuning_raw=%s, needs_review=%s, review_note=%s, genre=%s, name=%s, status='analyzed'
                            WHERE id=%s""",
                         (r["bpm"], r["bpm_given"], r["musical_key"], r["key_alt"], r["tuning_hz"], r["tuning_raw"],
                          r["needs_review"], r["review_note"], genre, meta.beat_name(b["original_name"] or ""), b["id"]))
            msg = (f"Analyzed: {meta.beat_name(b['original_name'] or '')}\n"
                   f"BPM {r['bpm']:g} | {r['musical_key']} (alt: {r['key_alt']}) | {r['tuning_hz']:g}Hz | genre: {genre}")
            if r["needs_review"]:
                msg += f"\nCheck: {r['review_note']}"
            buttons = None
            if not genre_given:
                msg += "\nNo genre given, using detroit. Tap to change:"
                buttons = [[{"text": g, "callback_data": f"genre:{b['id']}:{g}"} for g in GENRE_BUTTONS[i:i + 4]]
                           for i in range(0, len(GENRE_BUTTONS), 4)]
            notify(msg, buttons)
        except Exception as e:
            conn.execute("UPDATE beats SET status='error', review_note=%s WHERE id=%s", (str(e)[:300], b["id"]))
            notify(f"Could not analyze {b['original_name']}: {e}")


def next_slots(conn, n):
    per_day = int(S.get(conn, "uploads_per_day"))
    hours = list(S.get(conn, "upload_hours_utc"))[:max(per_day, 1)]
    taken = {r["scheduled"] for r in conn.execute(
        "SELECT scheduled FROM uploads WHERE platform='youtube' AND scheduled > now()").fetchall()}
    slots, day = [], now().date()
    while len(slots) < n:
        for h in hours:
            t = dt.datetime(day.year, day.month, day.day, h, tzinfo=dt.timezone.utc)
            if t > now() + dt.timedelta(hours=1) and t not in taken:
                slots.append(t)
        day += dt.timedelta(days=1)
    return slots[:n]


def step_schedule(conn):
    if not S.get(conn, "yt_enabled"):
        return  # beats wait as 'analyzed' until YouTube uploading is switched on
    beats = conn.execute("""SELECT id, genre FROM beats WHERE status='analyzed'
                            AND id NOT IN (SELECT beat_id FROM uploads WHERE platform='youtube') ORDER BY id""").fetchall()
    if not beats:
        return
    last = conn.execute("""SELECT b.genre FROM uploads u JOIN beats b ON b.id=u.beat_id
                           WHERE u.platform='youtube' ORDER BY u.scheduled DESC LIMIT 1""").fetchone()
    last_genre = last["genre"] if last else None
    ordered, pool = [], list(beats)
    while pool:  # avoid same genre twice in a row where possible
        pick = next((b for b in pool if b["genre"] != last_genre), pool[0])
        ordered.append(pick)
        pool.remove(pick)
        last_genre = pick["genre"]
    for b, slot in zip(ordered, next_slots(conn, len(ordered))):
        conn.execute("INSERT INTO uploads(beat_id, platform, scheduled, status) VALUES (%s,'youtube',%s,'scheduled')",
                     (b["id"], slot))
        conn.execute("UPDATE beats SET status='queued' WHERE id=%s", (b["id"],))
    notify(f"{len(ordered)} beat(s) added to the upload calendar.")


def step_finalize(conn):
    formulas = meta.load_formulas(conn)
    rows = conn.execute("""SELECT u.id uid, u.title_locked, b.* FROM uploads u JOIN beats b ON b.id=u.beat_id
                           WHERE u.platform='youtube' AND u.status='scheduled' AND u.scheduled < %s""",
                        (now() + FINALIZE_BEFORE,)).fetchall()
    for r in rows:
        if r["title_locked"]:
            conn.execute("UPDATE uploads SET status='final' WHERE id=%s", (r["uid"],))
            continue
        ranges = S.get(conn, "artist_bpm") or meta.DEFAULT_ARTIST_BPM
        artists, suffix, is_test, fits = meta.pick_formula(formulas, r["genre"], r["bpm"], ranges)
        if not fits:
            notify(f"BPM check: {r['name']} ({r['bpm']:g} BPM) doesn't fit the usual tempo of the {r['genre']} formula artists. "
                   f"Using: {' x '.join(artists)}. Change it in the dashboard Calendar if needed.")
        title = meta.make_title(artists, suffix, r["name"], meta.free_flag(S.get(conn, "free_tag")))
        desc = meta.make_description(r, artists, suffix, telegram=S.get(conn, "telegram_channel_url"),
                                     instagram=S.get(conn, "instagram_url"), beatstars_url=r.get("beatstars_url"))
        tags = meta.make_tags(artists, suffix)
        conn.execute("""UPDATE uploads SET title=%s, description=%s, tags=%s, formula=%s, is_test=%s, status='final'
                        WHERE id=%s""", (title, desc, tags, " x ".join(artists), is_test, r["uid"]))


def step_upload(conn):
    rows = conn.execute("""SELECT u.id uid, u.title, u.description, u.tags, u.scheduled, b.*
                           FROM uploads u JOIN beats b ON b.id=u.beat_id
                           WHERE u.platform='youtube' AND u.status='final' AND u.scheduled <= now()
                             AND (u.next_try IS NULL OR u.next_try <= now())
                           ORDER BY u.scheduled LIMIT 1""").fetchall()
    for r in rows:
        try:
            video = r["video_path"] or render.render(r["file_path"], r["name"], r["genre"], r["id"], conn)
            conn.execute("UPDATE beats SET video_path=%s WHERE id=%s", (video, r["id"]))
            from app.pipeline.youtube_api import upload
            vid = upload(video, r["title"], r["description"], r["tags"])
            conn.execute("UPDATE uploads SET status='uploaded', done=now(), external_id=%s WHERE id=%s", (vid, r["uid"]))
            conn.execute("UPDATE beats SET status='uploaded' WHERE id=%s", (r["id"],))
            notify(f"Uploaded to YouTube: {r['title']}")
            ch = S.get(conn, "tg_channel_id")
            if S.get(conn, "tg_channel_enabled") and ch:
                with open(r["file_path"], "rb") as f:
                    httpx.post(f"https://api.telegram.org/bot{TOKEN}/sendAudio",
                               data={"chat_id": ch, "caption": r["title"],
                                     "title": r["name"], "performer": "prod. " + " x ".join(r["producers"] or [])},
                               files={"audio": (os.path.basename(r["file_path"]), f)}, timeout=120)
        except Exception as e:
            row = conn.execute("""UPDATE uploads SET error=%s, attempts=attempts+1,
                                   next_try=now() + interval '30 minutes' * (attempts+1)
                                   WHERE id=%s RETURNING attempts""", (str(e)[:500], r["uid"])).fetchone()
            log_event(conn, "warn", "upload", f"{r['title']}: {e}")
            if row["attempts"] >= 4:
                conn.execute("UPDATE uploads SET status='error' WHERE id=%s", (r["uid"],))
                notify(f"Upload FAILED after 4 tries: {r['title']}\n{str(e)[:200]}\nThe next beat will continue as planned.")
            elif row["attempts"] == 1:
                notify(f"Upload problem (will retry): {r['title']}\n{str(e)[:200]}")


def run():
    conn = connect()
    ensure_schema(conn)
    log_event(conn, "info", "scheduler", "scheduler started")
    last_cov = 0
    while True:
        if time.time() - last_cov > 6 * 3600:
            last_cov = time.time()
            try:
                from app.pipeline import covers
                covers.run(conn)
            except Exception:
                log_event(conn, "warn", "covers", traceback.format_exc()[-300:])
        try:
            if not is_killed(conn):
                step_analyze(conn)
                step_schedule(conn)
                step_finalize(conn)
                step_upload(conn)
        except Exception:
            log_event(conn, "warn", "scheduler", traceback.format_exc()[-500:])
        time.sleep(60)


if __name__ == "__main__":
    run()
