"""Canonical genre / scene / mood vocabulary and mapping from raw tags or title words."""
import re

CANON = {
    "detroit": [r"detroit", r"\bdet\b", r"michigan"], "flint": [r"flint"], "memphis": [r"memphis"],
    "chicago": [r"chicago", r"\bchi\b", r"chiraq"], "scam": [r"scam"], "plugg": [r"plugg(?!nb)", r"\bplug\b"],
    "pluggnb": [r"pluggnb"], "rage": [r"rage"], "jerk": [r"jerk"], "cloud": [r"cloud"], "drill": [r"(?<!uk )(?<!ny )drill"],
    "uk drill": [r"uk drill"], "ny drill": [r"ny drill", r"new york drill", r"bronx"], "trap": [r"\btrap\b"],
    "west coast": [r"west coast", r"bay area", r"\bla\b", r"california", r"cali\b"], "atlanta": [r"atlanta", r"\batl\b"],
    "florida": [r"florida"], "texas": [r"texas", r"houston", r"dallas"], "milwaukee": [r"milwaukee"],
    "st louis": [r"st\.? louis", r"\bstl\b"], "kansas city": [r"kansas city"], "dmv": [r"\bdmv\b", r"maryland"],
    "louisiana": [r"louisiana", r"baton rouge", r"new orleans", r"kenner"], "richmond": [r"richmond", r"\brva\b"],
    "uptempo": [r"uptempo", r"up tempo", r"bounce", r"bouncy"], "boom bap": [r"boom ?bap"], "rnb": [r"\br&?b\b", r"rnb"],
    "underground": [r"underground"], "experimental": [r"experimental", r"abstract"],
    "dark": [r"\bdark\b", r"\bevil\b", r"sinister", r"menacing"], "sad": [r"\bsad\b", r"emotional", r"\bpain\b", r"melanchol"],
    "hard": [r"\bhard\b", r"aggressive", r"\bgrimy\b"], "piano": [r"piano"], "guitar": [r"guitar"],
    "sample": [r"\bsample\b", r"soul"], "chill": [r"\bchill\b", r"\bsmooth\b", r"\bmellow\b"], "dreamy": [r"dream", r"ethereal", r"ambient"],
}
_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in CANON.items()}


def map_text(text: str):
    t = text or ""
    return [k for k, pats in _COMPILED.items() if any(p.search(t) for p in pats)]
