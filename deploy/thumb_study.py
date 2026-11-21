"""Thumbnail study: which type beat thumbnails get more views per day?

Collects type beat videos for top artists of each niche, measures each thumbnail
(brightness, saturation, contrast, dark area, colour family, busyness/text) and compares
the best 20% vs the worst 20% by views/day *relative to that artist* (so big artists
don't dominate). Writes a text report + contact sheets to /data/visuals/study/ (shown at /study).

Run:  docker compose run --rm -e PYTHONPATH=/srv -v "$PWD/deploy:/srv/deploy" scheduler python deploy/thumb_study.py
"""
import sys
sys.path.insert(0, "/srv")
import os, random, statistics, subprocess, time
import numpy as np
import httpx
from app.common.db import connect
from app.scanner import youtube as yt

OUT = "/data/visuals/study"
NICHES = ["detroit", "flint", "scam", "memphis", "chicago", "plugg"]
PER_NICHE = 6
W, H = 160, 90
C = httpx.Client(timeout=20, follow_redirects=True)


def artists_for(conn, tag):
    return [r["name"] for r in conn.execute("""
        SELECT a.name FROM artists a JOIN artist_tags t ON t.artist_id=a.id AND t.tag=%s AND t.weight>=0.4
        JOIN LATERAL (SELECT vpd_med, sup30 FROM yt_metrics y WHERE y.artist_id=a.id ORDER BY measured DESC LIMIT 1) m ON true
        WHERE m.vpd_med >= 40 AND m.sup30 >= 5
        GROUP BY a.name, m.vpd_med ORDER BY m.vpd_med DESC LIMIT %s""", (tag, PER_NICHE)).fetchall()]


def pixels(jpg_path):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", jpg_path,
                          "-vf", f"crop=iw:iw*9/16,scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, timeout=30).stdout
    if len(raw) != W * H * 3:
        return None
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.float32) / 255


def features(px):
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    mx, mn = px.max(-1), px.min(-1)
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    # hue family of saturated pixels
    col = sat > 0.35
    hue = np.zeros_like(mx)
    d = np.maximum(mx - mn, 1e-6)
    hue = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    fam = lambda lo, hi: float(((hue >= lo) & (hue < hi) & col).mean())
    gy, gx = np.gradient(luma)
    edges = np.hypot(gx, gy) > 0.12
    return {
        "brightness": float(luma.mean()),
        "contrast": float(luma.std()),
        "saturation": float(sat.mean()),
        "dark_area": float((luma < 0.15).mean()),
        "bright_area": float((luma > 0.8).mean()),
        "red_orange": fam(0, 40) + fam(330, 361),
        "yellow_green": fam(40, 160),
        "blue_cyan": fam(160, 260),
        "purple_pink": fam(260, 330),
        "grey_bw": float((sat < 0.12).mean()),
        "busy_text": float(edges.mean()),           # lots of sharp edges = text / busy collage
        "center_focus": float(np.abs(luma[20:70, 50:110].mean() - luma.mean())),  # subject stands out in centre
    }


def collect(conn):
    rows = []
    os.makedirs(f"{OUT}/thumbs", exist_ok=True)
    for niche in NICHES:
        names = artists_for(conn, niche)
        print(f"{niche}: {', '.join(names) or 'no artists yet'}", flush=True)
        for a in names:
            vids = yt.search(f"{a} type beat", pages=2)
            vids = [v for v in vids if v.get("id") and "type beat" in v["t"].lower()
                    and a.lower().split()[0] in v["t"].lower() and 3 <= v["age"] < 400]
            if len(vids) < 8:
                continue
            for v in vids:
                v["vpd"] = v["views"] / max(v["age"], 1)
            med = statistics.median(v["vpd"] for v in vids) or 1
            for v in vids[:40]:
                p = f"{OUT}/thumbs/{v['id']}.jpg"
                if not os.path.exists(p):
                    try:
                        data = C.get(f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg").content
                        open(p, "wb").write(data)
                    except Exception:
                        continue
                    time.sleep(0.3)
                px = pixels(p)
                if px is None:
                    continue
                rows.append({"niche": niche, "artist": a, "id": v["id"], "title": v["t"], "vpd": v["vpd"],
                             "rel": v["vpd"] / med, "path": p, **features(px)})
            time.sleep(random.uniform(4, 8))
    return rows


def sheet(paths, out):
    paths = paths[:20]
    if not paths:
        return
    args = []
    for p in paths:
        args += ["-i", p]
    n = len(paths)
    cols = 5
    rows_ = (n + cols - 1) // cols
    f = "".join(f"[{i}:v]crop=iw:iw*9/16,scale=320:180[v{i}];" for i in range(n))
    layout = "|".join(f"{(i % cols) * 320}_{(i // cols) * 180}" for i in range(n))
    f += "".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=black"
    if n == 1:
        f = "[0:v]crop=iw:iw*9/16,scale=320:180"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex", f, "-frames:v", "1", "-q:v", "4", out],
                   timeout=120)


def report(rows):
    feats = ["brightness", "contrast", "saturation", "dark_area", "bright_area", "grey_bw", "red_orange",
             "yellow_green", "blue_cyan", "purple_pink", "busy_text", "center_focus"]
    lines = [f"THUMBNAIL STUDY - {len(rows)} type beat videos, {len({r['artist'] for r in rows})} artists", "",
             "Compares the best 20% vs worst 20% by views/day relative to the same artist.",
             "Numbers are averages 0-100. '>>' = clearly higher in winners, '<<' = clearly lower.", ""]
    for niche in ["ALL"] + NICHES:
        sub = rows if niche == "ALL" else [r for r in rows if r["niche"] == niche]
        if len(sub) < 25:
            continue
        sub = sorted(sub, key=lambda r: r["rel"], reverse=True)
        k = max(5, len(sub) // 5)
        top, bot = sub[:k], sub[-k:]
        lines.append(f"== {niche.upper()} ({len(sub)} videos, top {k} vs bottom {k}) ==")
        for f in feats:
            t = statistics.mean(r[f] for r in top) * 100
            b = statistics.mean(r[f] for r in bot) * 100
            sd = (statistics.pstdev(r[f] for r in sub) * 100) or 1
            mark = "  " if abs(t - b) < 0.5 else ">>" if (t - b) / sd > 0.35 else "<<" if (t - b) / sd < -0.35 else "  "
            lines.append(f"  {f:13s} winners {t:6.1f}   losers {b:6.1f}  {mark}")
        lines.append("")
        sheet([r["path"] for r in top], f"{OUT}/{niche}_top.jpg")
        sheet([r["path"] for r in bot], f"{OUT}/{niche}_bottom.jpg")
    open(f"{OUT}/report.txt", "w").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    conn = connect()
    rows = collect(conn)
    print(f"collected {len(rows)} thumbnails", flush=True)
    report(rows)
    print("Done. Open the dashboard -> /study", flush=True)
