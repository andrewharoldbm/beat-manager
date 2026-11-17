"""Title / description / tags generation.

Formulas live in settings('formulas') so they can be changed without code:
{
  "detroit": {"main": ["YN Jay", "FWC Big Key"], "suffix": "Detroit Type Beat",
              "tests": [["YN Jay","24Lik"], ["Rio Da Yung OG","FWC Big Key"], ["YN Jay","FWC Big Key","Rocaine"]],
              "test_share": 0.25},
  ...
}
The final title is chosen ~24h before upload (see scheduler), so it uses the latest data.
"""
import json
import random
import re
import os

DEFAULT_FORMULAS = {
    "detroit": {"main": ["YN Jay", "FWC Big Key"], "suffix": "Detroit Type Beat",
                "tests": [["YN Jay", "24Lik"], ["Rio Da Yung OG", "FWC Big Key"],
                          ["Rio Da Yung OG", "Tone4s"], ["YN Jay", "FWC Big Key", "Rocaine"]],
                "test_share": 0.25},
    "flint": {"main": ["Rio Da Yung OG", "Tone4s"], "suffix": "Flint Type Beat", "tests": [], "test_share": 0.25},
    "scam": {"main": ["BabyTron", "Buddyy"], "suffix": "Detroit Type Beat",
             "tests": [["BabyTron", "Tone4s"]], "test_share": 0.25},
}
FREE_TAG = os.environ.get("TITLE_FREE_TAG", "test")  # "on" | "off" | "test"
INSTAGRAM = os.environ.get("INSTAGRAM_URL", "instagram.com/conkretus")
TELEGRAM = os.environ.get("TELEGRAM_CHANNEL_URL", "")


def load_formulas(conn):
    row = conn.execute("SELECT value FROM settings WHERE key='formulas'").fetchone()
    if row and row["value"]:
        return row["value"]
    conn.execute("INSERT INTO settings(key,value) VALUES ('formulas', %s::jsonb) ON CONFLICT DO NOTHING",
                 (json.dumps(DEFAULT_FORMULAS),))
    return DEFAULT_FORMULAS


def beat_name(original_name: str) -> str:
    base = os.path.splitext(os.path.basename(original_name))[0]
    base = re.sub(r"^\d{9,}_", "", base)                 # timestamp prefix from bot
    base = re.sub(r"[@#][A-Za-z0-9_.$-]+", " ", base)    # tags
    base = re.sub(r"\b\d{2,3}\s*bpm\b", " ", base, flags=re.I)
    base = re.sub(r"\b([A-G][#b]?\s*(min|minor|maj|major|m))\b", " ", base, flags=re.I)
    base = re.sub(r"[_\-]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return (base or "Untitled").upper()


def pick_formula(formulas, genre):
    f = formulas.get(genre) or formulas.get("detroit")
    is_test = bool(f.get("tests")) and random.random() < f.get("test_share", 0.25)
    artists = random.choice(f["tests"]) if is_test else f["main"]
    return artists, f.get("suffix", ""), is_test


def make_title(artists, suffix, name, free: bool):
    t = " x ".join(artists) + " Type Beat"
    t = f'{t} - "{name}"'
    if suffix:
        t += f" | {suffix}"
    if free:
        t = "[FREE] " + t
    return t[:100]


def make_description(beat, artists, suffix, beatstars_url=None):
    prods = " x ".join(beat["producers"] or ["conkretus"])
    hz = f"{beat['tuning_hz']:g}Hz" if beat.get("tuning_hz") else ""
    lines = [
        f"BPM: {beat['bpm']:g} | Key: {beat['musical_key']}" + (f" | Tuning: {hz}" if hz else ""),
        f"prod. by {prods}",
        "",
        f"💰 Buy / Lease: {beatstars_url or 'link coming soon'}",
    ]
    if TELEGRAM:
        lines.append(f"📲 Telegram: {TELEGRAM}")
    lines.append(f"📸 Instagram: {INSTAGRAM}")
    lines += ["", "Free for non-profit use only. Credit: prod. by " + prods, ""]
    hashtags = ["#" + re.sub(r"[^A-Za-z0-9]", "", a.lower()) + "typebeat" for a in artists]
    if suffix:
        hashtags.append("#" + re.sub(r"[^A-Za-z0-9]", "", suffix.lower()))
    hashtags.append("#typebeat")
    lines.append(" ".join(hashtags[:15]))
    return "\n".join(lines)


def make_tags(artists, suffix, aliases=None):
    tags = []
    for a in artists:
        tags += [f"{a} type beat", a, f"free {a} type beat"]
    if suffix:
        tags += [suffix, f"free {suffix}"]
    for al in aliases or []:
        tags.append(f"{al} type beat")
    tags += ["type beat", "instrumental"]
    out, total = [], 0
    for t in dict.fromkeys(tags):
        if total + len(t) + 1 > 480:
            break
        out.append(t)
        total += len(t) + 1
    return out


def free_flag():
    if FREE_TAG == "on":
        return True
    if FREE_TAG == "off":
        return False
    return random.random() < 0.5
