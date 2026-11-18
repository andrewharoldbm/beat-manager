"""Producers behind our artists: Genius song credits -> producer names -> producer's own
YouTube channel/video descriptions -> public business email. Stored as category 'producer'
with who they worked with (for the loops system later)."""
import random
import re
import time
import httpx
from app.common.db import norm_key, log_event
from app.scanner import youtube
from app.scanner.emails import EMAIL_RE, JUNK, same_artist, artist_tags

G = httpx.Client(headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36",
                          "Accept": "application/json"}, timeout=20, follow_redirects=True)


def genius_songs(artist, n=12):
    r = G.get("https://genius.com/api/search/song", params={"q": artist, "per_page": n})
    out = []
    for sec in r.json().get("response", {}).get("sections", []):
        for h in sec.get("hits", []):
            s = h.get("result") or {}
            pa = (s.get("primary_artist") or {}).get("name", "")
            if s.get("id") and (same_artist(artist, pa) or norm_key(artist) in norm_key(s.get("artist_names", ""))):
                out.append(s["id"])
    return out[:n]


def genius_producers(song_id):
    s = G.get(f"https://genius.com/api/songs/{song_id}").json().get("response", {}).get("song", {})
    return [(p.get("name"), p.get("id")) for p in s.get("producer_artists") or [] if p.get("name")]


def genius_socials(gid):
    try:
        a = G.get(f"https://genius.com/api/artists/{gid}").json().get("response", {}).get("artist", {})
        return {"instagram": a.get("instagram_name"), "twitter": a.get("twitter_name")}
    except Exception:
        return {}


def producer_emails(name):
    found = set()
    for q in (f"{name} producer", f"prod by {name}", f"{name} type beat"):
        vids = youtube.search(q) or []
        own = [v for v in vids if v.get("id") and same_artist(name, v.get("ch"))][:2]
        for v in own:
            for e in EMAIL_RE.findall(youtube.description(v["id"])):
                e = e.strip(".").lower()
                if not JUNK.search(e):
                    found.add((e, v["id"]))
            time.sleep(random.uniform(2, 4))
        if found:
            break
        time.sleep(random.uniform(2, 4))
    return found


def upsert_producer(conn, name, gid):
    row = conn.execute("SELECT * FROM producers WHERE key=%s", (norm_key(name),)).fetchone()
    if row:
        return row
    soc = genius_socials(gid) if gid else {}
    return conn.execute("""INSERT INTO producers(name, key, genius_id, instagram, twitter)
                           VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                        (name, norm_key(name), gid, soc.get("instagram"), soc.get("twitter"))).fetchone()


def link(conn, prod, artist):
    conn.execute("""INSERT INTO producer_artists(producer_id, artist_id) VALUES (%s,%s)
                    ON CONFLICT DO NOTHING""", (prod["id"], artist["id"]))


PROD_RE = re.compile(r"(?:\bprod(?:uced)?\.?\s*(?:by)?\s*[:\-]?\s*)([A-Za-z0-9$.&'_ -]{2,60}?)(?=[)\]\n|,]|$)", re.I)


def split_names(raw):
    out = []
    for n in re.split(r"\s+(?:x|&|and)\s+|,|/|\+", raw, flags=re.I):
        n = re.sub(r"\s+", " ", n).strip(" .-_'")
        if 2 <= len(n) <= 30 and not re.search(r"\b(official|video|music|audio|lyrics|dir|shot|by)\b", n, re.I):
            out.append(n)
    return out


def youtube_producers(artist):
    """Fallback without Genius: 'prod. by X' in the artist's own video titles/descriptions."""
    names = set()
    vids = youtube.search(f'{artist["name"]} official video') or []
    own = [v for v in vids if v.get("id") and same_artist(artist["name"], v.get("ch"))][:6]
    for v in own:
        for m in PROD_RE.findall(v.get("t", "")):
            names.update(split_names(m))
    for v in own[:3]:
        for m in PROD_RE.findall(youtube.description(v["id"])):
            names.update(split_names(m))
        time.sleep(random.uniform(2, 4))
    return {n: None for n in names if norm_key(n) != norm_key(artist["name"])}


def process_artist(conn, artist):
    prods = {}
    try:
        for sid in genius_songs(artist["name"]):
            for name, gid in genius_producers(sid):
                if norm_key(name) != norm_key(artist["name"]):
                    prods[name] = gid
            time.sleep(random.uniform(1.5, 3))
    except Exception as e:
        if not getattr(process_artist, "_warned", False):
            log_event(conn, "warn", "producers", f"genius unavailable, using YouTube credits: {str(e)[:120]}")
            process_artist._warned = True
    if not prods:
        prods = youtube_producers(artist)
    for name, gid in prods.items():
        p = upsert_producer(conn, name, gid)
        link(conn, p, artist)
    conn.execute("UPDATE artists SET producers_checked=now() WHERE id=%s", (artist["id"],))
    return len(prods)


def find_emails(conn, batch=5):
    n = 0
    for p in conn.execute("""SELECT p.* FROM producers p WHERE p.email_checked IS NULL
                             ORDER BY (SELECT count(*) FROM producer_artists x WHERE x.producer_id=p.id) DESC
                             LIMIT %s""", (batch,)).fetchall():
        try:
            emails = producer_emails(p["name"])
        except Exception as e:
            log_event(conn, "warn", "producers", f"{p['name']}: {str(e)[:150]}")
            emails = set()
        worked = [r["name"] for r in conn.execute(
            """SELECT a.name FROM producer_artists x JOIN artists a ON a.id=x.artist_id
               WHERE x.producer_id=%s ORDER BY a.priority LIMIT 15""", (p["id"],)).fetchall()]
        tags = sorted({t for r in conn.execute("SELECT artist_id FROM producer_artists WHERE producer_id=%s",
                                               (p["id"],)).fetchall() for t in artist_tags(conn, r["artist_id"])})[:10]
        for e, vid in emails:
            r = conn.execute("""INSERT INTO contacts(email, name, role, category, source, tags, worked_with, found_via, notes)
                                VALUES (%s,%s,'producer','producer',%s,%s,%s,%s,%s)
                                ON CONFLICT (email) DO NOTHING RETURNING id""",
                             (e, p["name"], f"prod:yt:{vid}", tags, worked, worked[0] if worked else None,
                              " ".join(f"{k}: {v}" for k, v in (("ig", p["instagram"]), ("x", p["twitter"])) if v))).fetchone()
            n += bool(r)
        conn.execute("UPDATE producers SET email_checked=now(), email_found=%s WHERE id=%s", (bool(emails), p["id"]))
        time.sleep(random.uniform(4, 8))
    return n


def step(conn, artists=8, emails=10):
    from app.common import settings as S
    if not S.get(conn, "prod_reset_v1"):  # retry artists that failed while Genius was blocked
        conn.execute("""UPDATE artists a SET producers_checked=NULL WHERE producers_checked IS NOT NULL
                        AND NOT EXISTS (SELECT 1 FROM producer_artists x WHERE x.artist_id=a.id)""")
        S.set_(conn, "prod_reset_v1", True)
    for a in conn.execute("""SELECT * FROM artists WHERE producers_checked IS NULL
                               AND (coalesce(discovered_via,'') <> 'yt_title' OR title_mentions >= 3)
                             ORDER BY priority, id LIMIT %s""", (artists,)).fetchall():
        try:
            process_artist(conn, a)
        except Exception as e:
            log_event(conn, "warn", "producers", f"genius {a['name']}: {str(e)[:150]}")
            conn.execute("UPDATE artists SET producers_checked=now() WHERE id=%s", (a["id"],))
    n = find_emails(conn, emails)
    if n:
        log_event(conn, "info", "producers", f"found {n} new producer emails")
