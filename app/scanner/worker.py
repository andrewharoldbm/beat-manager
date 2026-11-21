"""Continuous artist discovery + YouTube type-beat market measurement.

Loop:
  1. pick next artist (priority, then oldest scan)
  2. search "<artist> type beat" (relevance + this month)
  3. store metrics, extract new artist names from titles (snowball)
  4. occasionally expand via Last.fm similar artists
Paced slowly to avoid YouTube blocks.
"""
import os
import re
import time
import random
import statistics
from app.common.db import connect, norm_key, upsert_artist, is_killed, log_event
from app.scanner import youtube, lastfm

SEEDS = {
    1: ["Veeze", "Babyface Ray", "YN Jay", "FWC Big Key", "24Lik", "RealRichIzzo", "392 Lil Head",
        "WTM Scoob", "WTM Milt", "WTM Solid", "Peezy", "BabyTron", "Damedot", "Baby Smoove",
        "Skilla Baby", "Sada Baby", "Icewear Vezzo", "42 Dugg", "Tee Grizzley", "Lelo", "DaeMoney",
        "Top$ide", "Drego", "Tay B", "GMO Stax", "Cash Kidd", "Kasher Quon", "Allstar JR", "Rocaine",
        "NUK", "Shittyboyz", "Buddyy", "Tone4s"],
    2: ["Rio Da Yung OG", "RMC Mike", "Louie Ray", "Babyfxce E", "Grindhard E", "Lil M.U.", "MG Sleepy"],
    3: ["Key Glock", "Young Dolph", "Big Scarr", "Pooh Shiesty", "Babyceo Trell", "Duke Deuce"],
    4: ["Lil Durk", "G Herbo", "Chief Keef", "FBG Duck", "Lil Noonie", "BabyChiefDoit", "Lud Foe"],
    5: ["Teejayx6", "Punchmade Dev", "Lucki", "Tony Shhnow", "El Cousteau"],
}
STOP = re.compile(r"\b(free|prod\.?.*|20\d\d|hard|dark|sad|detroit|flint|michigan|memphis|chicago|uptempo|piano|sample|instrumental)\b", re.I)
SLEEP_MIN = float(os.environ.get("SCAN_SLEEP_MIN", "4"))
SLEEP_MAX = float(os.environ.get("SCAN_SLEEP_MAX", "9"))


def extract_names(title: str):
    s = re.sub(r"\(.*?\)|\[.*?\]|[\"“”'’‘].*?[\"“”'’‘]", " ", title)
    m = re.match(r"^(.*?)type\s*beat", s, re.I)
    if not m:
        return []
    parts = re.split(r"\s+x\s+|\s*,\s*|\s+&\s+|\s+\+\s+|\s+vs\.?\s+|\s*\|\s*|\s+-\s+", m.group(1), flags=re.I)
    out = []
    for p in parts:
        p = STOP.sub("", p).strip(" -|")
        if 3 <= len(p) <= 24 and not re.search(r"beat|type", p, re.I):
            out.append(p)
    return out


def measure(name, rel, mon):
    nm = norm_key(name)
    has = lambda v: nm in norm_key(v["t"]) and re.search(r"type\s*beat", v["t"], re.I)
    R = [v for v in rel if has(v)]
    M = [v for v in mon if has(v)]
    seen, yr = set(), []
    for v in R + M:
        if v["t"] in seen:
            continue
        seen.add(v["t"])
        if 3 <= v["age"] <= 365:
            yr.append(v["views"] / v["age"])
    top = sorted((v["views"] for v in R), reverse=True)
    return {
        "rel_n": len(R), "sup30": len(M), "sup7": sum(1 for v in M if v["age"] <= 7),
        "vpd_med": statistics.median(yr) if yr else 0, "vpd_top": max(yr) if yr else 0,
        "med_month": int(statistics.median([v["views"] for v in M])) if M else 0,
        "top5_avg": int(sum(top[:5]) / 5) if top else 0,
    }


def title_tags(conn, artist, videos):
    from collections import Counter
    from app.common.genres import map_text
    nm = norm_key(artist["name"])
    titles = {v["t"] for v in videos if nm in norm_key(v["t"])}
    if len(titles) < 3:
        return
    cnt = Counter(g for t in titles for g in set(map_text(t)))
    for g, n in cnt.items():
        w = n / len(titles)
        if w >= 0.1:
            conn.execute("""INSERT INTO artist_tags(artist_id, tag, weight, source) VALUES (%s,%s,%s,'yt_title')
                            ON CONFLICT (artist_id, tag, source) DO UPDATE SET weight=EXCLUDED.weight, updated=now()""",
                         (artist["id"], g, round(w, 3)))


def seed(conn):
    for prio, names in SEEDS.items():
        for n in names:
            upsert_artist(conn, n, "seed", priority=prio)


def next_artist(conn):
    return conn.execute(
        """SELECT id, name, priority, keep FROM artists
           WHERE (hidden IS NULL OR hidden = 'suspicious numbers') AND (discovered_via <> 'yt_title' OR title_mentions >= 2)
             AND (last_scanned IS NULL OR last_scanned < now() - (CASE WHEN priority<=2 THEN interval '7 days' ELSE interval '30 days' END))
           ORDER BY priority, last_scanned NULLS FIRST, title_mentions DESC LIMIT 1""").fetchone()


def expand_lastfm(conn, artist):
    for name, match in lastfm.similar(artist["name"]):
        aid = upsert_artist(conn, name, "lastfm", priority=min(9, artist["priority"] + 1))
        if aid:
            conn.execute("""INSERT INTO artist_links(a,b,kind,weight) VALUES (%s,%s,'lastfm_similar',%s)
                            ON CONFLICT (a,b,kind) DO UPDATE SET weight=EXCLUDED.weight""",
                         (artist["id"], aid, match))


def run():
    conn = connect()
    seed(conn)
    from app.common import settings as S
    if not S.get(conn, "age_fix_v2"):  # views/day were 0 because upload dates weren't parsed: re-measure
        n0 = conn.execute("""UPDATE artists a SET last_scanned=NULL WHERE last_scanned IS NOT NULL AND EXISTS
                             (SELECT 1 FROM yt_metrics y WHERE y.artist_id=a.id AND coalesce(y.vpd_med,0)=0)""").rowcount
        S.set_(conn, "age_fix_v2", True)
        log_event(conn, "info", "scanner", f"re-measuring {n0} artists after date parsing fix")
    if not S.get(conn, "sup_fix_v1"):  # competition was capped at ~20: re-measure those with full first pages
        n1 = conn.execute("""UPDATE artists a SET last_scanned=NULL WHERE last_scanned IS NOT NULL AND EXISTS
                             (SELECT 1 FROM yt_metrics y WHERE y.artist_id=a.id AND y.sup30 >= 15)""").rowcount
        S.set_(conn, "sup_fix_v1", True)
        log_event(conn, "info", "scanner", f"re-measuring {n1} artists to count competition past 20")
    if not S.get(conn, "generic_hide_v1"):  # hide artists whose name is just a common word (e.g. "VIBES")
        from app.common.generic_names import is_generic
        ids = [r["id"] for r in conn.execute("SELECT id, name FROM artists WHERE hidden IS NULL AND NOT keep").fetchall() if is_generic(r["name"])]
        if ids:
            conn.execute("UPDATE artists SET hidden='generic word' WHERE id = ANY(%s)", (ids,))
        S.set_(conn, "generic_hide_v1", True)
        log_event(conn, "info", "scanner", f"hid {len(ids)} artists with generic names")
    log_event(conn, "info", "scanner", "scanner started")
    n = 0
    while True:
        if is_killed(conn):
            time.sleep(60)
            continue
        a = next_artist(conn)
        if not a:
            time.sleep(300)
            continue
        q = a["name"] + " type beat"
        rel = youtube.search(q)
        time.sleep(random.uniform(1, 3))
        mon = youtube.search(q, youtube.FILTER_MONTH)
        # first page full of this artist's type beats -> read more pages to count real competition (up to ~100)
        nm = norm_key(a["name"])
        if sum(1 for v in mon if nm in norm_key(v["t"])) >= 15:
            mon = youtube.search(q, youtube.FILTER_MONTH, pages=5)
        if not rel and not mon:
            # possibly blocked: back off
            log_event(conn, "warn", "scanner", f"empty results for {a['name']} ({youtube.LAST_STATUS['reason'] or 'no matches'}), backing off")
            time.sleep(600)
        m = measure(a["name"], rel, mon)
        # huge demand but almost nobody uploads "<name> type beat" = the name is just a word people search
        from app.common.generic_names import is_generic
        if not a.get("keep") and (is_generic(a["name"]) or (m["vpd_med"] >= 800 and m["sup30"] <= 2)):
            why = "generic word" if is_generic(a["name"]) else "suspicious numbers"
            conn.execute("UPDATE artists SET hidden=%s WHERE id=%s", (why, a["id"]))
        elif a.get("hidden") is None:
            pass
        else:
            conn.execute("UPDATE artists SET hidden=NULL WHERE id=%s AND hidden='suspicious numbers'", (a["id"],))
        conn.execute("""INSERT INTO yt_metrics(artist_id, rel_n, sup7, sup30, vpd_med, vpd_top, med_month, top5_avg)
                        VALUES (%(id)s,%(rel_n)s,%(sup7)s,%(sup30)s,%(vpd_med)s,%(vpd_top)s,%(med_month)s,%(top5_avg)s)""",
                     {"id": a["id"], **m})
        conn.execute("UPDATE artists SET last_scanned=now() WHERE id=%s", (a["id"],))
        try:
            title_tags(conn, a, rel + mon)
        except Exception:
            pass
        for v in rel + mon:
            names = extract_names(v["t"])
            for nm in names:
                upsert_artist(conn, nm, "yt_title", priority=min(9, a["priority"] + 1), mentions_inc=1)
        if a["priority"] <= 5 and n % 3 == 0:
            expand_lastfm(conn, a)
        n += 1
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


if __name__ == "__main__":
    run()
