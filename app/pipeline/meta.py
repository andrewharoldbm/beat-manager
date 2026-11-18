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
    base = re.sub(r"\b\d{3}(\.\d+)?\s*hz\b", " ", base, flags=re.I)
    base = re.sub(r"\b(detune[d]?|tuned|tagged|untagged|master(ed)?|final|mixdown|export|v\d+)\b", " ", base, flags=re.I)
    base = re.sub(r"\b([A-G][#b]?\s*(min|minor|maj|major|m))\b", " ", base, flags=re.I)
    base = re.sub(r"[_\-]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return (base or "Untitled").upper()


DEFAULT_ARTIST_BPM = {"BabyTron": [120, 160], "Buddyy": [120, 165]}


def bpm_fits(artists, bpm, ranges):
    """True if the beat's BPM (or its half/double time) is inside every artist's range."""
    if not bpm:
        return True
    for a in artists:
        r = ranges.get(a)
        if r and not any(r[0] <= x <= r[1] for x in (bpm, bpm / 2, bpm * 2)):
            return False
    return True


def pick_formula(formulas, genre, bpm=None, ranges=None):
    """Returns (artists, suffix, is_test, fits). Prefers formulas whose artists fit the beat's BPM."""
    ranges = ranges if ranges is not None else DEFAULT_ARTIST_BPM
    f = formulas.get(genre) or formulas.get("detroit")
    is_test = bool(f.get("tests")) and random.random() < f.get("test_share", 0.25)
    order = ([random.choice(f["tests"])] if is_test else []) + [f["main"]] + list(f.get("tests", []))
    for artists in order:
        if bpm_fits(artists, bpm, ranges):
            return artists, f.get("suffix", ""), artists != f["main"], True
    for g2, f2 in formulas.items():  # another genre's formula that fits
        for artists in [f2["main"]] + list(f2.get("tests", [])):
            if bpm_fits(artists, bpm, ranges):
                return artists, f2.get("suffix", ""), True, False
    return f["main"], f.get("suffix", ""), False, False


def make_title(artists, suffix, name, free: bool):
    t = " x ".join(artists) + " Type Beat"
    t = f'{t} - "{name}"'
    if suffix:
        t += f" | {suffix}"
    if free:
        t = "[FREE] " + t
    return t[:100]


def make_description(beat, artists, suffix, beatstars_url=None, telegram="", instagram="instagram.com/conkretus"):
    prods = " x ".join(beat["producers"] or ["conkretus"])
    hz = f"{beat['tuning_hz']:g}Hz" if beat.get("tuning_hz") else ""
    lines = [
        f"BPM: {beat['bpm']:g} | Key: {beat['musical_key']}" + (f" | Tuning: {hz}" if hz else ""),
        f"prod. by {prods}",
        "",
        (f"💰 Buy / Lease: {beatstars_url}" if beatstars_url else "💰 To purchase: DM @conkretus on Telegram"),
    ]
    if telegram:
        lines.append(f"📲 Telegram: {telegram}")
    if instagram:
        lines.append(f"📸 Instagram: {instagram}")
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


def free_flag(mode="test"):
    if mode == "on":
        return True
    if mode == "off":
        return False
    return random.random() < 0.5
