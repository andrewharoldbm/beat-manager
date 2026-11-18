"""Owner-supplied cover for one beat: validated image -> 1:1 jpg in /data/visuals/custom."""
import os
import subprocess

DIR = os.path.join(os.environ.get("VISUALS_DIR", "/data/visuals"), "custom")
MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"RIFF")


def save(conn, beat_id, data: bytes) -> str:
    if not data or len(data) > 15_000_000 or not data.startswith(MAGIC):
        raise ValueError("not a jpg/png/webp image (max 15MB)")
    os.makedirs(DIR, exist_ok=True)
    tmp = os.path.join(DIR, f"tmp_{beat_id}")
    out = os.path.join(DIR, f"beat_{beat_id}.jpg")
    open(tmp, "wb").write(data)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-vf",
                        "crop='min(iw,ih)':'min(iw,ih)',scale=1500:1500", "-frames:v", "1", "-q:v", "2", out],
                       check=True, timeout=120)
    finally:
        os.remove(tmp)
    # use it, and re-render if a video was already made
    conn.execute("UPDATE beats SET cover_path=%s, video_path=NULL WHERE id=%s", (out, beat_id))
    return out
