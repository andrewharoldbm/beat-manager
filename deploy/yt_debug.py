"""Shows what the scanner sees for one YouTube search (to debug 0 views/day)."""
import sys
sys.path.insert(0, "/srv")
import json, re
from app.scanner import youtube as y
q = " ".join(sys.argv[1:]) or "icewear vezzo type beat"
r = y._client.get("https://www.youtube.com/results", params={"search_query": q, "sp": "EgIIBA%3D%3D", "hl": "en", "gl": "US"})
m = re.search(r"var ytInitialData = (\{.*?\});</script>", r.text)
parts = []
def walk(o):
    if isinstance(o, dict):
        if "metadataParts" in o:
            parts.append([(p.get("text") or {}).get("content") for p in o["metadataParts"]][:3])
        for v in o.values(): walk(v)
    elif isinstance(o, list):
        for v in o: walk(v)
if m:
    walk(json.loads(m.group(1)))
print("status", r.status_code, "| raw parts sample:", parts[:6])
res = y.search(q, "EgIIBA%3D%3D")
print("parsed:", [(x.get("views"), x.get("age")) for x in (res or [])[:8]], "| reason:", getattr(y, "LAST_STATUS", ""))
