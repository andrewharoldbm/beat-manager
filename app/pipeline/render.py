"""Video render: still image + beat audio -> mp4 (YouTube long-form 1920x1080 by default).

Visuals are taken from VISUALS_DIR (images the owner provides). If none exist,
a plain dark background with the beat name is generated.
"""
import os
import random
import subprocess

VISUALS_DIR = os.environ.get("VISUALS_DIR", "/data/visuals")
OUT_DIR = os.environ.get("RENDER_DIR", "/data/renders")
FORMAT = os.environ.get("VIDEO_FORMAT", "16:9")  # "16:9" long-form, "9:16" shorts
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _size():
    return (1080, 1920) if FORMAT == "9:16" else (1920, 1080)


def pick_visual(genre: str | None):
    if not os.path.isdir(VISUALS_DIR):
        return None
    cands = []
    for sub in ([genre] if genre else []) + [""]:
        d = os.path.join(VISUALS_DIR, sub) if sub else VISUALS_DIR
        if os.path.isdir(d):
            cands = [os.path.join(d, f) for f in os.listdir(d)
                     if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
            if cands:
                break
    return random.choice(cands) if cands else None


def render(audio_path: str, name: str, genre: str | None, beat_id: int) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    w, h = _size()
    out = os.path.join(OUT_DIR, f"beat_{beat_id}_{w}x{h}.mp4")
    img = pick_visual(genre)
    safe = name.replace("'", "").replace(":", " ")[:40]
    if img:
        vin = ["-loop", "1", "-i", img]
        # fit the whole cover (usually 1:1) and add black bars on the sides
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p")
    else:
        vin = ["-f", "lavfi", "-i", f"color=c=0x0d0d0d:s={w}x{h}"]
        vf = (f"drawtext=fontfile={FONT}:text='{safe}':fontcolor=white:fontsize={int(h/14)}:"
              f"x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *vin, "-i", audio_path,
           "-vf", vf, "-r", "1", "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
           "-c:a", "aac", "-b:a", "320k", "-shortest", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, timeout=900)
    return out
