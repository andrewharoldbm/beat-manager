"""Beat analysis: BPM, key (minor/major + alternative), tuning (snapped to 430/432/440/452).

Rules from the owner:
- beats are almost always minor; if analysis says major -> mark for review and give minor relative as alt
- tuning reference points: 430, 432, 440, 452 Hz; snap to nearest, flag if far from all
- BPM written in filename wins unless analysis strongly disagrees (then flag)
"""
import re
import numpy as np
import librosa

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler key profiles
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
TUNINGS = [430.0, 432.0, 440.0, 452.0]


def bpm_from_name(name: str):
    m = re.search(r"(\d{2,3})\s*bpm", name, re.I)
    return float(m.group(1)) if m else None


def tags_from_text(text: str):
    producers = re.findall(r"@([A-Za-z0-9_.$]+)", text)
    genres = [g.lower() for g in re.findall(r"#([A-Za-z0-9_-]+)", text)]
    return producers, genres


def detect_key(y, sr, tuning_frac):
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, tuning=tuning_frac)
    prof = chroma.mean(axis=1)
    scores = []
    for i in range(12):
        scores.append((np.corrcoef(np.roll(MAJOR, i), prof)[0, 1], NOTES[i], "major"))
        scores.append((np.corrcoef(np.roll(MINOR, i), prof)[0, 1], NOTES[i], "minor"))
    scores.sort(reverse=True)
    return scores


def analyze(path: str, original_name: str = "", caption: str = ""):
    y, sr = librosa.load(path, sr=22050, mono=True, duration=240)
    # tuning: fraction of a semitone relative to A440
    frac = float(librosa.estimate_tuning(y=y, sr=sr))
    hz_raw = 440.0 * 2 ** (frac / 12)
    hz = min(TUNINGS, key=lambda t: abs(t - hz_raw))
    notes = []
    review = False
    if abs(hz - hz_raw) > 3:
        review = True
        notes.append(f"tuning {hz_raw:.1f} Hz is far from 430/432/440/452")

    tempo = librosa.feature.tempo(y=y, sr=sr, aggregate=np.median)
    bpm_a = float(np.atleast_1d(tempo)[0])
    bpm_given = bpm_from_name(original_name + " " + caption)
    bpm = bpm_given or round(bpm_a)
    if bpm_given:
        # accept half/double time relations
        ratios = [bpm_a / bpm_given, bpm_a * 2 / bpm_given, bpm_a / 2 / bpm_given]
        if min(abs(1 - r) for r in ratios) > 0.06:
            review = True
            notes.append(f"BPM in name {bpm_given:g} vs detected {bpm_a:.0f}")

    scores = detect_key(y, sr, frac)
    best = scores[0]
    best_minor = next(s for s in scores if s[2] == "minor")
    if best[2] == "major":
        review = True
        # relative minor of a major key = 9 semitones up
        rel = NOTES[(NOTES.index(best[1]) + 9) % 12]
        key, key_alt = f"{best_minor[1]} minor", f"{best[1]} major (relative {rel} minor)"
        notes.append("analysis suggests major; review")
    else:
        key = f"{best[1]} minor"
        maj = next(s for s in scores if s[2] == "major")
        key_alt = f"{maj[1]} major"

    producers, genres = tags_from_text(original_name + " " + caption)
    return {
        "bpm": bpm, "bpm_detected": round(bpm_a, 1), "bpm_given": bpm_given,
        "musical_key": key, "key_alt": key_alt,
        "tuning_hz": hz, "tuning_raw": round(hz_raw, 1),
        "needs_review": review, "review_note": "; ".join(notes),
        "producers": producers or ["conkretus"], "genres": genres,
    }
