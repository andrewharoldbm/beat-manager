"""Auto covers: public-domain / CC0 photos from Openverse (no attribution or permission needed),
cropped 1:1 and graded dark so the channel looks consistent. Runs daily from the scheduler."""
import os
import random
import subprocess
import httpx
from app.common.db import log_event

VIS = os.environ.get("VISUALS_DIR", "/data/visuals")
PER_GENRE = 40
QUERIES = {
    "detroit": ["detroit night", "detroit street", "abandoned factory", "city night street", "abandoned house",
                "industrial ruins", "highway night", "car night"],
    "flint": ["abandoned house", "empty street night", "water tower", "rusty car", "small town night"],
    "memphis": ["memphis night", "neon sign night", "dark alley", "old motel"],
    "chicago": ["chicago night", "train night city", "skyline night", "elevated train"],
    "scam": ["money cash", "credit card", "computer dark room", "phone night", "luxury car night"],
    "dark": ["dark forest", "fog night", "black and white city", "empty room dark"],
    "sad": ["rain window", "lonely night", "empty street rain"],
    "": ["city night", "street lights night", "dark car", "urban night"],
}
C = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "beat-manager/1.0"})


def search(q, page=1):
    r = C.get("https://api.openverse.org/v1/images/", params={
        "q": q, "license": "cc0,pdm", "size": "large", "mature": "false", "page_size": 20, "page": page})
    r.raise_for_status()
    return r.json().get("results", [])


def grade(src, dst):
    # square crop from center, 1500px, darker + slightly desaturated + grain
    vf = ("crop='min(iw,ih)':'min(iw,ih)',scale=1500:1500,"
          "eq=brightness=-0.08:contrast=1.15:saturation=0.75,noise=alls=8:allf=t")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf", vf, "-frames:v", "1", "-q:v", "3", dst],
                   check=True, timeout=120)


def fill_genre(conn, genre, need):
    got = 0
    qs = QUERIES.get(genre, QUERIES[""])[:]
    random.shuffle(qs)
    d = os.path.join(VIS, genre) if genre else VIS
    os.makedirs(d, exist_ok=True)
    for q in qs:
        if got >= need:
            break
        for it in search(q, random.randint(1, 3)):
            if got >= need:
                break
            if not it.get("url") or (it.get("width") or 0) < 1000 or (it.get("height") or 0) < 1000:
                continue
            if conn.execute("SELECT 1 FROM covers WHERE source_id=%s", (it["id"],)).fetchone():
                continue
            tmp = f"/tmp/cov_{it['id']}"
            try:
                data = C.get(it["url"]).content
                if len(data) < 50_000:
                    continue
                open(tmp, "wb").write(data)
                out = os.path.join(d, f"ov_{it['id'][:12]}.jpg")
                grade(tmp, out)
                conn.execute("""INSERT INTO covers(source_id, genre, path, url, creator, license, query)
                                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                             (it["id"], genre, out, it.get("foreign_landing_url"), it.get("creator"), it.get("license"), q))
                got += 1
            except Exception:
                pass
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
    return got


def run(conn):
    total = 0
    for g in QUERIES:
        have = conn.execute("SELECT count(*) n FROM covers WHERE genre=%s", (g,)).fetchone()["n"]
        if have < PER_GENRE:
            try:
                total += fill_genre(conn, g, min(10, PER_GENRE - have))
            except Exception as e:
                log_event(conn, "warn", "covers", f"{g or 'general'}: {str(e)[:150]}")
    if total:
        log_event(conn, "info", "covers", f"added {total} public-domain covers")
    return total
