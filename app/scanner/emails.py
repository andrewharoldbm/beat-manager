"""Find public business emails of artists (from their own YouTube video descriptions) and tag
each contact with the artist's genres. Only emails the artist published for business/booking."""
import re
import time
import random
from app.common.db import norm_key, log_event
from app.scanner import youtube

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
JUNK = re.compile(r"noreply|no-reply|example\.|@(youtube|google|distrokid|tunecore|vevo|unitedmasters|empire\.com|sentry)", re.I)


def same_artist(artist, channel):
    a, c = norm_key(artist), norm_key(channel or "")
    return bool(a) and (a in c or c in a or c.replace("vevo", "") == a)


def find_for_artist(conn, artist):
    found = set()
    vids = youtube.search(f'{artist["name"]} official video') or []
    for v in [v for v in vids if v.get("id") and same_artist(artist["name"], v.get("ch"))][:3]:
        d = youtube.description(v["id"])
        for e in EMAIL_RE.findall(d):
            e = e.strip(".").lower()
            if not JUNK.search(e):
                found.add((e, v["id"]))
        time.sleep(random.uniform(2, 4))
    n = 0
    for e, vid in found:
        r = conn.execute("""INSERT INTO contacts(email, name, role, artist_id, source, tags)
                            VALUES (%s,%s,'artist',%s,%s,%s) ON CONFLICT (email) DO NOTHING RETURNING id""",
                         (e, artist["name"], artist["id"], f"yt:{vid}", artist_tags(conn, artist["id"]))).fetchone()
        n += bool(r)
    conn.execute("UPDATE artists SET email_checked=now() WHERE id=%s", (artist["id"],))
    return n


def artist_tags(conn, artist_id):
    return [r["tag"] for r in conn.execute(
        """SELECT tag, max(weight) w FROM artist_tags WHERE artist_id=%s GROUP BY tag
           HAVING max(weight) >= 0.3 ORDER BY w DESC LIMIT 8""", (artist_id,)).fetchall()]


def tag_contacts(conn):
    """Link imported contacts to artists by name and copy the artist's genres onto the contact."""
    for c in conn.execute("""SELECT id, name FROM contacts WHERE artist_id IS NULL AND name IS NOT NULL
                             AND name <> '' LIMIT 500""").fetchall():
        a = conn.execute("SELECT id FROM artists WHERE key=%s", (norm_key(c["name"]),)).fetchone()
        if a:
            conn.execute("UPDATE contacts SET artist_id=%s WHERE id=%s", (a["id"], c["id"]))
    for c in conn.execute("""SELECT id, artist_id FROM contacts WHERE artist_id IS NOT NULL
                             AND (tags IS NULL OR tags = '{}')""").fetchall():
        t = artist_tags(conn, c["artist_id"])
        if t:
            conn.execute("UPDATE contacts SET tags=%s WHERE id=%s", (t, c["id"]))


def step(conn, batch=15):
    """Priority artists with demand first; each artist checked once a quarter."""
    rows = conn.execute("""
        SELECT a.* FROM artists a
        LEFT JOIN LATERAL (SELECT vpd_med FROM yt_metrics y WHERE y.artist_id=a.id ORDER BY measured DESC LIMIT 1) m ON true
        WHERE (a.email_checked IS NULL OR a.email_checked < now()-interval '90 days')
          AND (coalesce(a.discovered_via, '') <> 'yt_title' OR a.title_mentions >= 3)
        ORDER BY a.priority, coalesce(m.vpd_med, 0) DESC LIMIT %s""", (batch,)).fetchall()
    total = 0
    for a in rows:
        try:
            total += find_for_artist(conn, a)
        except Exception as e:
            log_event(conn, "warn", "emails", f"{a['name']}: {str(e)[:150]}")
            conn.execute("UPDATE artists SET email_checked=now() WHERE id=%s", (a["id"],))
        time.sleep(random.uniform(4, 8))
    if total:
        log_event(conn, "info", "emails", f"found {total} new artist emails")
    return total
