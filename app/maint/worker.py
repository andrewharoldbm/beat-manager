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


def notify(text, buttons=None):
    if TOKEN and OWNER:
        body = {"chat_id": int(OWNER), "text": text, "disable_web_page_preview": True}
        if buttons:
            body["reply_markup"] = {"inline_keyboard": buttons}
        try:
            httpx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=body, timeout=20)
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
        SELECT u.formula, count(*) videos, round(avg(s.views))::float avg_views
        FROM uploads u JOIN LATERAL (SELECT views FROM upload_stats x WHERE x.upload_id=u.id
             AND x.measured <= u.done + interval '8 days' ORDER BY measured DESC LIMIT 1) s ON true
        WHERE u.status='uploaded' AND u.done < now()-interval '7 days'
        GROUP BY u.formula ORDER BY avg_views DESC""").fetchall()


def apply_proposal(conn, p):
    from app.pipeline import meta
    from app.common import settings as S
    f = meta.load_formulas(conn)
    pl = p["payload"]
    g = p["genre"]
    if p["kind"] == "promote_test" and g in f:
        old_main = f[g]["main"]
        f[g]["main"] = pl["new_main"]
        f[g]["tests"] = [t for t in f[g].get("tests", []) if t != pl["new_main"]] + [old_main]
        S.set_(conn, "formulas", f)
    conn.execute("UPDATE proposals SET status='applied', decided=now() WHERE id=%s", (p["id"],))
    log_event(conn, "info", "autopilot", f"applied proposal {p['id']}: {p['text']}")


def check_tests(conn):
    """Promote a test formula if it clearly beats the main one (>=5 videos each, +20% avg views at 7 days)."""
    from app.pipeline import meta
    from app.common import settings as S
    perf = {r["formula"]: r for r in formula_performance(conn)}
    f = meta.load_formulas(conn)
    for g, cfg in f.items():
        main = " x ".join(cfg["main"])
        m = perf.get(main)
        if not m or m["videos"] < 5:
            continue
        for t in cfg.get("tests", []):
            tp = perf.get(" x ".join(t))
            if tp and tp["videos"] >= 5 and tp["avg_views"] > m["avg_views"] * 1.2:
                exists = conn.execute("SELECT 1 FROM proposals WHERE status='open' AND genre=%s", (g,)).fetchone()
                if exists:
                    break
                text = (f"[{g}] Test formula '{' x '.join(t)}' gets {int(tp['avg_views'])} avg views vs main "
                        f"'{main}' {int(m['avg_views'])}. Make it the main formula?")
                row = conn.execute("INSERT INTO proposals(kind, genre, payload, text) VALUES ('promote_test',%s,%s::jsonb,%s) RETURNING id",
                                   (g, __import__('json').dumps({"new_main": t}), text)).fetchone()
                hrs = int(S.get(conn, "autopilot_hours"))
                notify(text + f"\nIf you don't answer in {hrs}h, autopilot applies it.",
                       [[{"text": "Approve", "callback_data": f"prop:{row['id']}:yes"},
                         {"text": "Reject", "callback_data": f"prop:{row['id']}:no"}]])
                break
    hrs = int(S.get(conn, "autopilot_hours"))
    for p in conn.execute("SELECT * FROM proposals WHERE status='open' AND created < now() - make_interval(hours => %s)", (hrs,)).fetchall():
        apply_proposal(conn, p)
        notify(f"Autopilot applied: {p['text']}")


def best_candidates(conn, genre, exclude, n=3):
    return conn.execute("""
        SELECT a.name, m.vpd_med, m.sup30 FROM artists a
        JOIN LATERAL (SELECT * FROM yt_metrics y WHERE y.artist_id=a.id ORDER BY measured DESC LIMIT 1) m ON true
        WHERE m.rel_n >= 8 AND m.sup30 <= 8 AND m.vpd_med >= 30 AND NOT (lower(a.name) = ANY(%s))
          AND EXISTS (SELECT 1 FROM artist_tags t WHERE t.artist_id=a.id AND t.tag=%s AND t.weight >= 0.4)
        ORDER BY m.vpd_med / (m.sup30 + 2) DESC LIMIT %s""", ([e.lower() for e in exclude], genre, n)).fetchall()


def check_niches(conn):
    """If the niche (B) artist of a main formula gets saturated or loses demand, propose a replacement."""
    import json as _json
    from app.pipeline import meta
    from app.common import settings as S
    f = meta.load_formulas(conn)
    used = {a for cfg in f.values() for a in cfg["main"]} | {a for cfg in f.values() for t in cfg.get("tests", []) for a in t}
    for g, cfg in f.items():
        if len(cfg["main"]) < 2:
            continue
        b = cfg["main"][1]
        hist = conn.execute("""SELECT y.sup30, y.vpd_med, y.measured FROM yt_metrics y JOIN artists a ON a.id=y.artist_id
                               WHERE lower(a.name)=lower(%s) ORDER BY y.measured DESC""", (b,)).fetchall()
        if not hist:
            continue
        now_m = hist[0]
        old = next((h for h in hist if h["measured"] < now_m["measured"] - dt.timedelta(days=21)), None)
        reason = None
        if now_m["sup30"] >= 15:
            reason = f"{b} now has {now_m['sup30']}+ type beats per month (too much competition)"
        elif old and old["vpd_med"] and now_m["vpd_med"] < old["vpd_med"] * 0.7:
            reason = f"demand for {b} dropped from {old['vpd_med']:.0f} to {now_m['vpd_med']:.0f} views/day"
        if not reason or conn.execute("SELECT 1 FROM proposals WHERE status='open' AND genre=%s", (g,)).fetchone():
            continue
        cands = best_candidates(conn, g, list(used))
        if not cands:
            continue
        c0 = cands[0]
        new_main = [cfg["main"][0], c0["name"]] + cfg["main"][2:]
        text = (f"[{g}] {reason}. Suggest replacing {b} with {c0['name']} "
                f"({c0['vpd_med']:.0f} views/day, {c0['sup30']} uploads/30d). New main: {' x '.join(new_main)}")
        row = conn.execute("INSERT INTO proposals(kind, genre, payload, text) VALUES ('promote_test',%s,%s::jsonb,%s) RETURNING id",
                           (g, _json.dumps({"new_main": new_main}), text)).fetchone()
        hrs = int(S.get(conn, "autopilot_hours"))
        notify(text + f"\nIf you don't answer in {hrs}h, autopilot applies it.",
               [[{"text": "Approve", "callback_data": f"prop:{row['id']}:yes"},
                 {"text": "Reject", "callback_data": f"prop:{row['id']}:no"}]])


def youtube_watch(conn):
    """Detect channel changes or videos not uploaded by the system (possible account compromise)."""
    from app.common import settings as S
    try:
        from app.pipeline.youtube_api import service
        yt = service()
    except Exception:
        return
    if not yt:
        return
    ch = yt.channels().list(part="snippet,contentDetails,statistics", mine=True).execute().get("items", [])
    if not ch:
        return
    ch = ch[0]
    snap = {"title": ch["snippet"].get("title"), "custom_url": ch["snippet"].get("customUrl"),
            "description": ch["snippet"].get("description", "")[:500]}
    base = S.get(conn, "yt_baseline")
    if base and base != snap:
        changed = [k for k in snap if snap[k] != base.get(k)]
        notify(f"SECURITY: YouTube channel {', '.join(changed)} changed. If this wasn't you, send /kill now "
               f"and change your Google password.")
        log_event(conn, "alert", "security", f"youtube channel changed: {changed}")
    S.set_(conn, "yt_baseline", snap)
    uploads_pl = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="contentDetails", playlistId=uploads_pl, maxResults=25).execute().get("items", [])
    ours = {r["external_id"] for r in conn.execute("SELECT external_id FROM uploads WHERE external_id IS NOT NULL").fetchall()}
    known = set(S.get(conn, "yt_known_foreign") or [])
    for it in items:
        vid = it["contentDetails"]["videoId"]
        if vid not in ours and vid not in known:
            if base:  # skip the very first run (old private videos)
                notify("SECURITY: a video appeared on the channel that the system did not upload. "
                       "If this wasn't you, send /kill and change your Google password.")
                log_event(conn, "alert", "security", f"foreign video {vid}")
            known.add(vid)
    S.set_(conn, "yt_known_foreign", sorted(known))


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
    bl = conn.execute("""SELECT
        count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM uploads u WHERE u.beat_id=b.id AND u.status='uploaded')) yt_left,
        count(*) FILTER (WHERE b.id NOT IN (SELECT unnest(beat_ids) FROM email_packs WHERE beat_ids IS NOT NULL)) mail_left,
        count(*) FILTER (WHERE b.status='error' OR b.needs_review) problems
        FROM beats b""").fetchone()
    weak = conn.execute("""SELECT p.id, count(l.*) n, count(*) FILTER (WHERE l.status LIKE 'opened%%') o
        FROM email_packs p JOIN email_log l ON l.pack_id=p.id AND l.kind='first'
        GROUP BY p.id HAVING count(l.*) >= 5 ORDER BY p.id DESC LIMIT 6""").fetchall()
    lines = ["Weekly report", "",
             f"Artists in DB: {a['n']} (+{a['new']} this week), measured: {a['s']}",
             f"Uploads this week: {up}, in calendar: {q}",
             f"Emails sent this week: {em['n']}, opened: {em['o']}, contacts who replied (total): {rep}",
             f"Beats not on YouTube yet: {bl['yt_left']}, not in any email pack yet: {bl['mail_left']}, need attention: {bl['problems']}"]
    if weak:
        lines += ["Recent packs (opened/sent): " + ", ".join(f"#{r['id']} {r['o']}/{r['n']}" for r in weak)]
    lines += ["",
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
    last_prop = last_report = last_sec = last_stats = last_mail = None
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
                check_tests(conn)
                check_niches(conn)
                last_stats = now
            if not last_mail or now - last_mail > dt.timedelta(minutes=30):
                from app.scanner import emails as EM
                EM.tag_contacts(conn)
                EM.step(conn)
                from app.scanner import producers as PR
                PR.step(conn)
                from app.email.contacts import verify_pending
                verify_pending(conn)
                last_mail = now
            if not last_sec or now - last_sec > dt.timedelta(minutes=55):
                security_checks(conn)
                try:
                    youtube_watch(conn)
                except Exception as e:
                    log_event(conn, "warn", "security", f"youtube watch failed: {str(e)[:200]}")
                last_sec = now
        except Exception:
            log_event(conn, "warn", "maint", traceback.format_exc()[-500:])
        time.sleep(120)


if __name__ == "__main__":
    run()
