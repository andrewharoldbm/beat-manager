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
                       cookies={"CONSENT": "YES+cb.20240101-00-p0.en+FX+999", "SOCS": "CAI", "PREF": "hl=en&gl=US"})
LAST_STATUS = {"reason": ""}


def _num(text: str) -> int:
    t = (text or "").lower().replace("\xa0", " ")
    m = re.search(r"(\d[\d.,\s]*)\s*(k\b|tūkst|tsd|mil\b|m\b|mln|mio|b\b|mlrd|md)?", t)
    if not m:
        return 0
    num, suf = m.group(1).strip(), m.group(2)
    mult = 1
    if suf:
        mult = 1e3 if suf in ("k", "tūkst", "tsd", "mil") else 1e9 if suf in ("b", "mlrd", "md") else 1e6
        n = float(num.replace(" ", "").replace(",", "."))
    else:
        n = float(re.sub(r"[.,\s]", "", num) or 0)
    return int(n * mult)


AGE_UNITS = [  # (regex, days) - English plus the languages YouTube may answer in for an EU server IP
    (r"sec|sek|segund|secon", 0.5), (r"min", 0.5),
    (r"hour|hr\b|val\b|valand|std|stund|godz|heure|\bora|uur|hora", 0.5),
    (r"week|sav|woche|semaine|settiman|tydz|semana|\bw\b", 7),
    (r"month|mėn|men\.|monat|mois|\bmes|mies|maand|\bmo\b", 30),
    (r"year|\bmet|\bm\.|jahr|\ban\b|\bans\b|ann|año|\blat|\brok|jaar", 365),
    (r"day|dien|\bd\.|\bd\b|tag|jour|giorn|\bdag|dni|día|dias", 1),
]


def _age_days(text: str) -> float:
    t = (text or "").lower()
    m = re.search(r"(\d+)", t)
    if not m:
        return 9999
    n = int(m.group(1))
    # compact English: "5h ago", "2w ago", "1mo ago", "3y ago"
    cm = re.search(r"(\d+)\s*(s|sec|m|min|h|hr|d|w|wk|mo|y|yr)s?\b", t)
    if cm:
        u = cm.group(2)
        return {"s": 0.5, "sec": 0.5, "m": 0.5, "min": 0.5, "h": 0.5, "hr": 0.5, "d": n, "w": n * 7, "wk": n * 7,
                "mo": n * 30, "y": n * 365, "yr": n * 365}[u]
    for rx, d in AGE_UNITS:
        if re.search(rx, t):
            return 0.5 if d == 0.5 else n * d
    return 9999


def _walk(o, out):
    if isinstance(o, dict):
        if "videoRenderer" in o:
            v = o["videoRenderer"]
            out.append({
                "id": v.get("videoId"),
                "ch": ((v.get("ownerText") or {}).get("runs") or [{}])[0].get("text", ""),
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
                nums = []
                for r in rows:
                    for p in r.get("metadataParts", []):
                        txt = (p.get("text") or {}).get("content", "") or ""
                        lab = p.get("accessibilityLabel", "") or txt
                        if re.search(r"\d", txt) and len(txt) < 40:
                            nums.append((txt, lab))
                # first short numeric part = views, next = age (language independent)
                for txt, lab in nums:
                    if views == 0 and (re.search(r"view|peržiūr|aufruf|vue|visualizz|wyświetl|weergave|visualiza", lab, re.I)
                                       or _age_days(lab) == 9999):
                        views = _num(txt)
                    elif age == 9999 and _age_days(lab) != 9999:
                        age = _age_days(lab)
                ch = ""
                try:
                    ch = rows[0]["metadataParts"][0]["text"]["content"]
                except Exception:
                    pass
                out.append({"id": l.get("contentId"), "ch": ch,
                            "t": (md.get("title") or {}).get("content", ""), "views": views, "age": age})
            return
        for v in o.values():
            _walk(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out)


def _cont(o):
    """Find the next-page continuation token in a search response."""
    if isinstance(o, dict):
        if "continuationCommand" in o and isinstance(o["continuationCommand"], dict):
            return o["continuationCommand"].get("token")
        for v in o.values():
            t = _cont(v)
            if t:
                return t
    elif isinstance(o, list):
        for v in o:
            t = _cont(v)
            if t:
                return t
    return None


def search(query: str, sp: str | None = None, retries: int = 3, pages: int = 1):
    url = "https://www.youtube.com/results?hl=en&gl=US&search_query=" + quote_plus(query)
    if sp:
        url += "&sp=" + sp
    for attempt in range(retries):
        try:
            r = _client.get(url)
            if r.status_code == 429:
                time.sleep(60 * (attempt + 1))
                continue
            if "consent.youtube.com" in str(r.url) or "consent.google" in r.text[:5000]:
                LAST_STATUS["reason"] = "consent page"
                return []
            m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text, re.S)
            if not m:
                LAST_STATUS["reason"] = f"no data (HTTP {r.status_code})"
                return []
            LAST_STATUS["reason"] = ""
            out = []
            data = json.loads(m.group(1))
            _walk(data, out)
            if pages > 1:
                ver = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', r.text)
                key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', r.text)
                tok = _cont(data)
                for _ in range(pages - 1):
                    if not (tok and ver):
                        break
                    time.sleep(1.5 + random.random() * 2)
                    j = _client.post("https://www.youtube.com/youtubei/v1/search",
                                     params={"prettyPrint": "false", **({"key": key.group(1)} if key else {})},
                                     json={"context": {"client": {"clientName": "WEB", "clientVersion": ver.group(1),
                                                                  "hl": "en", "gl": "US"}},
                                           "continuation": tok}).json()
                    _walk(j, out)
                    tok = _cont(j)
            return out
        except Exception:
            time.sleep(5 + random.random() * 5)
    return []


def description(video_id: str) -> str:
    r = _client.get(f"https://www.youtube.com/watch?v={video_id}")
    m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', r.text)
    if not m:
        return ""
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:
        return m.group(1)
