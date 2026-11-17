import os
import time
import httpx

KEY = os.environ.get("LASTFM_API_KEY", "")
_c = httpx.Client(timeout=20)


def call(method: str, **params):
    if not KEY:
        return {}
    for attempt in range(3):
        try:
            r = _c.get("https://ws.audioscrobbler.com/2.0/",
                       params={"method": method, "api_key": KEY, "format": "json", **params})
            if r.status_code == 429:
                time.sleep(30)
                continue
            return r.json()
        except Exception:
            time.sleep(3)
    return {}


def similar(name: str, limit: int = 100):
    d = call("artist.getsimilar", artist=name, limit=limit, autocorrect=1)
    return [(a["name"], float(a.get("match", 0))) for a in d.get("similarartists", {}).get("artist", [])]


def info(name: str):
    d = call("artist.getinfo", artist=name, autocorrect=1).get("artist")
    if not d:
        return None
    return {
        "name": d.get("name", name),
        "listeners": int(d.get("stats", {}).get("listeners", 0) or 0),
        "tags": [t["name"].lower() for t in d.get("tags", {}).get("tag", [])],
    }
