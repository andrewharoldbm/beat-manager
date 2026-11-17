"""Reads public YouTube search result pages and extracts video title/views/age.

Only text is parsed. Nothing is downloaded or executed.
"""
import json
import re
import time
import random
import httpx
from urllib.parse import quote_plus

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
FILTER_MONTH = "EgIIBA%3D%3D"
_client = httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True,
                       cookies={"CONSENT": "YES+1", "PREF": "hl=en&gl=US"})


def _num(text: str) -> int:
    t = (text or "").lower().replace(",", "")
    m = re.search(r"([\d.]+)\s*([kmb])?", t)
    if not m:
        return 0
    n = float(m.group(1))
    return int(n * {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2) or "", 1))


def _age_days(text: str) -> float:
    t = (text or "").lower()
    m = re.search(r"(\d+)", t)
    n = int(m.group(1)) if m else 1
    if re.search(r"second|minute|hour", t):
        return 0.5
    if "day" in t:
        return n
    if "week" in t:
        return n * 7
    if "month" in t:
        return n * 30
    if "year" in t:
        return n * 365
    return 9999


def _walk(o, out):
    if isinstance(o, dict):
        if "videoRenderer" in o:
            v = o["videoRenderer"]
            out.append({
                "t": (v.get("title", {}).get("runs") or [{}])[0].get("text", ""),
                "views": _num(v.get("viewCountText", {}).get("simpleText", "0")),
                "age": _age_days(v.get("publishedTimeText", {}).get("simpleText", "")),
            })
            return
        if "lockupViewModel" in o:
            l = o["lockupViewModel"]
            md = (l.get("metadata") or {}).get("lockupMetadataViewModel")
            if l.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO" and md:
                views, age = 0, 9999
                rows = (md.get("metadata") or {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                for r in rows:
                    for p in r.get("metadataParts", []):
                        txt = (p.get("text") or {}).get("content", "")
                        lab = p.get("accessibilityLabel", "") or txt
                        if re.search(r"view", lab, re.I):
                            views = _num(txt)
                        elif re.search(r"ago", lab, re.I):
                            age = _age_days(txt)
                out.append({"t": (md.get("title") or {}).get("content", ""), "views": views, "age": age})
            return
        for v in o.values():
            _walk(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out)


def search(query: str, sp: str | None = None, retries: int = 3):
    url = "https://www.youtube.com/results?hl=en&gl=US&search_query=" + quote_plus(query)
    if sp:
        url += "&sp=" + sp
    for attempt in range(retries):
        try:
            r = _client.get(url)
            if r.status_code == 429:
                time.sleep(60 * (attempt + 1))
                continue
            m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
            if not m:
                return []
            out = []
            _walk(json.loads(m.group(1)), out)
            return out
        except Exception:
            time.sleep(5 + random.random() * 5)
    return []
