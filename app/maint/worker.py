"""Maintenance: artist genre tagging, neighbor propagation, weekly Telegram report, security alerts."""
import datetime as dt
import os
import time
import traceback
import httpx
from app.common.db import connect, ensure_schema, log_event
from app.common.genres import map_text
from app.scanner import lastfm

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER = os.environ.get("OWNER_CHAT_ID", "")


def notify(text):
    if TOKEN and OWNER:
        try:
            httpx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                       json={"chat_id": int(OWNER), "text": text, "disable_web_page_preview": True}, timeout=20)
        except Exception:
            pass


def upsert_tag(conn, aid, tag, w, source):
    conn.execute("""INSERT INTO artist_tags(artist_id, tag, weight, source) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (artist_id, tag, source) DO UPDATE SET weight=EXCLUDED.weight, updated=now()""",
                 (aid, tag, round(float(w), 3), source))


def tag_lastfm(conn, limit=60):
    rows = conn.execute("""SELECT a.id, a.name FROM artists a WHERE a.last_scanned IS NOT NULL
                           AND NOT EXISTS (SELECT 1 FROM artist_tags t WHERE t.artist_id=a.id AND t.source='lastfm')
                           AND coalesce(a.lastfm_listeners, 0) >= 0 ORDER BY a.priority, a.id LIMIT %s""", (limit,)).fetchall()
    for r in rows:
        info = lastfm.info(r["name"])
        if not info:
            upsert_tag(conn, r["id"], "_none", 0, "lastfm")
            continue
        conn.execute("UPDATE artists SET lastfm_listeners=%s WHERE id=%s", (info["listeners"], r["id"]))
        for rank, raw in enumerate(info["tags"][:8]):
            w = max(0.2, 1 - rank * 0.12)
            for g in map_text(raw):
                upsert_tag(conn, r["id"], g, w, "lastfm")
        upsert_tag(conn, r["id"], "_none", 0, "lastfm")
        time.sleep(0.3)


def propagate(conn):
    """Each artist inherits 50% of the average tag weights of its Last.fm-similar neighbors."""
    conn.execute("DELETE FROM artist_tags WHERE source='neighbors'")
    conn.execute("""INSERT INTO artist_tags(artist_id, tag, weight, source)
        SELECT l.a, t.tag, LEAST(1, 0.5 * sum(t.weight * l.weight) / NULLIF(sum(l.weight),0)), 'neighbors'
        FROM artist_links l JOIN artist_tags t ON t.artist_id=l.b AND t.source IN ('lastfm','yt_title') AND t.tag <> '_none'
        WHERE l.kind='lastfm_similar' GROUP BY l.a, t.tag
        HAVING 0.5 * sum(t.weight * l.weight) / NULLIF(sum(l.weight),0) >= 0.05
        ON CONFLICT (artist_id, tag, source) DO UPDATE SET weight=EXCLUDED.weight, updated=now()""")


def video_stats(conn):
    key = os.environ.get("YT_API_KEY", "")
    rows = conn.execute("SELECT id, external_id FROM uploads WHERE status='uploaded' AND external_id IS NOT NULL").fetchall()
    for i in range(0, len(rows), 50):
        chunk = rows[i:i + 50]
        ids = ",".join(r["external_id"] for r in chunk)
        try:
            d = httpx.get("https://www.googleapis.com/youtube/v3/videos",
                          params={"part": "statistics", "id": ids, "key": key}, timeout=30).json()
        except Exception:
            continue
        by = {it["id"]: it.get("statistics", {}) for it in d.get("items", [])}
        for r in chunk:
            st = by.get(r["external_id"])
            if st is not None:
                conn.execute("INSERT INTO upload_stats(upload_id, views, likes, comments) VALUES (%s,%s,%s,%s)",
                             (r["id"], int(st.get("viewCount", 0)), int(st.get("likeCount", 0)), int(st.get("commentCount", 0))))


def formula_performance(conn):
    """Average views per video at ~7 days old, per formula (used for testing decisions)."""
    return conn.execute("""
        SELECT u.formula, count(*) videos, round(avg(s.views)) avg_views
        FROM uploads u JOIN LATERAL (SELECT views FROM upload_stats x WHERE x.upload_id=u.id
             AND x.measured <= u.done + interval '8 days' ORDER BY measured DESC LIMIT 1) s ON true
        WHERE u.status='uploaded' AND u.done < now()-interval '7 days'
        GROUP BY u.formula ORDER BY avg_views DESC""").fetchall()


def weekly_report(conn):
    a = conn.execute("""SELECT count(*) n, count(*) FILTER (WHERE first_seen > now()-interval '7 days') new,
                        count(last_scanned) s FROM artists""").fetchone()
    top = conn.execute("""
        SELECT a.name, m.vpd_med, m.sup30 FROM artists a
        JOIN LATERAL (SELECT * FROM yt_metrics y WHERE y.artist_id=a.id ORDER BY measured DESC LIMIT 1) m ON true
        WHERE m.rel_n >= 5 AND m.sup30 <= 8 AND m.vpd_med >= 20
          AND EXISTS (SELECT 1 FROM artist_tags t WHERE t.artist_id=a.id AND t.tag IN ('detroit','flint') AND t.weight >= 0.3)
        ORDER BY m.vpd_med / (m.sup30 + 2) DESC LIMIT 10""").fetchall()
    up = conn.execute("SELECT count(*) n FROM uploads WHERE status='uploaded' AND done > now()-interval '7 days'").fetchone()["n"]
    q = conn.execute("SELECT count(*) n FROM uploads WHERE status IN ('scheduled','final')").fetchone()["n"]
    em = conn.execute("""SELECT count(*) n, count(*) FILTER (WHERE status LIKE 'opened%%') o FROM email_log
                         WHERE sent_at > now()-interval '7 days'""").fetchone()
    rep = conn.execute("SELECT count(*) n FROM contacts WHERE replied").fetchone()["n"]
    lines = ["Weekly report", "",
             f"Artists in DB: {a['n']} (+{a['new']} this week), measured: {a['s']}",
             f"Uploads this week: {up}, in calendar: {q}",
             f"Emails sent this week: {em['n']}, opened: {em['o']}, contacts who replied (total): {rep}", "",
             "Top Detroit/Flint niche opportunities (views/day, uploads last 30d):"]
    lines += [f"- {r['name']}: {r['vpd_med']:.0f}/day, {r['sup30']} uploads" for r in top] or ["- not enough data yet"]
    perf = formula_performance(conn)
    if perf:
        lines += ["", "Title formulas (avg views after 7 days):"]
        lines += [f"- {r['formula']}: {int(r['avg_views'])} views ({r['videos']} videos)" for r in perf[:8]]
    notify("\n".join(lines))


def security_checks(conn):
    n = conn.execute("""SELECT count(*) n FROM events WHERE kind='login' AND level='warn'
                        AND at > now()-interval '1 hour'""").fetchone()["n"]
    if n >= 5:
        notify(f"Security alert: {n} failed dashboard logins in the last hour. If this wasn't you, consider /kill.")
        log_event(conn, "alert", "security", f"{n} failed logins in 1h")


def run():
    conn = connect()
    ensure_schema(conn)
    log_event(conn, "info", "maint", "maintenance worker started")
    last_prop = last_report = last_sec = last_stats = None
    while True:
        try:
            tag_lastfm(conn)
            now = dt.datetime.now(dt.timezone.utc)
            if not last_prop or now - last_prop > dt.timedelta(hours=12):
                propagate(conn)
                last_prop = now
            if now.weekday() == 0 and now.hour == 8 and (not last_report or now - last_report > dt.timedelta(hours=20)):
                weekly_report(conn)
                last_report = now
            if not last_stats or now - last_stats > dt.timedelta(hours=6):
                video_stats(conn)
                last_stats = now
            if not last_sec or now - last_sec > dt.timedelta(minutes=55):
                security_checks(conn)
                last_sec = now
        except Exception:
            log_event(conn, "warn", "maint", traceback.format_exc()[-500:])
        time.sleep(120)


if __name__ == "__main__":
    run()
