"""Producer tag parsing. Short/typo forms map to the official credit name."""
import re

DEFAULT_ALIASES = {"conkretus": "conkretus", "conkre": "conkretus", "cnk": "conkretus",
                   "konkretus": "conkretus", "conkretas": "conkretus"}
TAG = re.compile(r"@([A-Za-z0-9_$-]+)")


def aliases(conn=None):
    a = dict(DEFAULT_ALIASES)
    if conn is not None:
        try:
            from app.common import settings as S
            a.update({k.lower(): v for k, v in (S.get(conn, "producer_aliases") or {}).items()})
        except Exception:
            pass
    return a


def canon(name, conn=None):
    n = name.strip().lstrip("@").rstrip(".")
    return aliases(conn).get(n.lower(), n)


def parse(text, conn=None):
    """'going out 173bpm @cnk @rsd' -> ['conkretus', 'rsd'] (conkretus always first)."""
    out = []
    text = re.sub(r"\.(mp3|wav|aiff?|flac)\b", " ", text or "", flags=re.I)
    for n in TAG.findall(text):
        c = canon(n, conn)
        if c.lower() not in [o.lower() for o in out]:
            out.append(c)
    out = [o for o in out if o.lower() != "conkretus"]
    return ["conkretus"] + out
