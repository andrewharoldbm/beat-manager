"""Beat Manager web dashboard (owner only, password + 2FA)."""
import datetime as dt
import json
import os
import re
import time
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import Response, HTMLResponse, RedirectResponse, JSONResponse
from jinja2 import Environment, DictLoader, select_autoescape
from app.common.db import connect, ensure_schema, is_killed, log_event
from app.common import settings as S
from app.web import auth
from app.web.templates import ALL
from app.pipeline import meta

INBOX = os.environ.get("BEATS_INBOX", "/data/beats/inbox")
MAX_UPLOAD = 300 * 1024 * 1024
env = Environment(loader=DictLoader(ALL), autoescape=select_autoescape(default=True))
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_conn = None
COOKIE = "bm_session"


def db():
    global _conn
    if _conn is None or _conn.closed:
        _conn = connect()
        ensure_schema(_conn)
    return _conn


def page(name, request, **ctx):
    return HTMLResponse(env.get_template(name).render(nav=True, active=request.url.path, **ctx))


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path not in ("/login", "/health") and not path.startswith("/o/") and not auth.valid_session(db(), request.cookies.get(COOKIE)):
        if path.startswith("/api/"):
            return JSONResponse({"error": "auth"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:"
    return resp


PIXEL = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


@app.get("/o/{tok}.gif")
def opened(tok: str):
    from fastapi.responses import Response
    if re.fullmatch(r"[A-Za-z0-9_-]{8,40}", tok):
        c = db()
        row = c.execute("UPDATE email_log SET status='opened:'||%s WHERE status=%s RETURNING contact_id",
                        (tok, "sent:" + tok)).fetchone()
        if row:
            c.execute("UPDATE contacts SET opened_count=opened_count+1 WHERE id=%s", (row["contact_id"],))
    return Response(PIXEL, media_type="image/gif", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    return HTMLResponse(env.get_template("login").render(nav=False, configured=auth.configured(db()), error=error))


@app.post("/login")
def login(request: Request, password: str = Form(...), code: str = Form(...)):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[-1].strip()
    if auth.rate_limited(ip):
        return login_page(request, error="Too many attempts. Try again in 15 minutes.")
    if not auth.check_login(db(), ip, password, code.strip()):
        log_event(db(), "warn", "login", f"failed login from {ip}")
        time.sleep(1)
        return login_page(request, error="Wrong password or code.")
    log_event(db(), "info", "login", f"owner logged in from {ip}")
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, auth.make_session(db()), httponly=True, secure=True, samesite="strict",
                 max_age=auth.SESSION_HOURS * 3600)
    return r


@app.post("/logout")
def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE)
    return r


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    c = db()
    a = c.execute("SELECT count(*) n, count(last_scanned) s FROM artists").fetchone()
    m24 = c.execute("SELECT count(*) n FROM yt_metrics WHERE measured > now()-interval '24 hours'").fetchone()["n"]
    bs = c.execute("SELECT status, count(*) n FROM beats GROUP BY status ORDER BY status").fetchall()
    nxt = c.execute("""SELECT u.scheduled, coalesce(u.title, b.name) t, u.status FROM uploads u JOIN beats b ON b.id=u.beat_id
                       WHERE u.status IN ('scheduled','final') ORDER BY u.scheduled LIMIT 5""").fetchall()
    return page("overview", request, a=a, m24=m24, beat_status=[(r["status"], r["n"]) for r in bs],
                beats_total=sum(r["n"] for r in bs), next=nxt, killed=is_killed(c), yt=S.get(c, "yt_enabled"),
                uploaded=c.execute("SELECT count(*) n FROM uploads WHERE status='uploaded'").fetchone()["n"],
                review=c.execute("SELECT name, review_note FROM beats WHERE needs_review AND status<>'uploaded' ORDER BY id DESC LIMIT 10").fetchall(),
                events=c.execute("SELECT * FROM events ORDER BY at DESC LIMIT 12").fetchall())


DEFAULT_GENRES = ["detroit", "flint", "scam", "memphis", "chicago", "rage", "plugg", "jerk", "drill", "other"]


@app.get("/beats", response_class=HTMLResponse)
def beats(request: Request):
    c = db()
    used = [r["genre"] for r in c.execute("SELECT DISTINCT genre FROM beats WHERE genre IS NOT NULL").fetchall()]
    genres = list(dict.fromkeys(DEFAULT_GENRES + list(meta.load_formulas(c).keys()) + used))
    return page("beats", request, beats=c.execute("SELECT * FROM beats WHERE status <> 'deleted' ORDER BY id DESC LIMIT 500").fetchall(), genres=genres)


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), relpath: str = Form(""), genre: str = Form("")):
    name = os.path.basename(file.filename or "beat.mp3")
    if not name.lower().endswith((".mp3", ".wav")):
        return JSONResponse({"error": "only mp3/wav"}, status_code=400)
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        return JSONResponse({"error": "too large"}, status_code=413)
    if not (data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or data[:4] == b"RIFF"):
        return JSONResponse({"error": "not audio"}, status_code=400)
    parts = [p for p in relpath.replace("\\", "/").split("/") if p]
    folder_genre = parts[-2].lower() if len(parts) >= 2 else ""
    g = (folder_genre or genre or "").strip().lower() or None
    if g:
        g = re.sub(r"[^a-z0-9_-]", "", g)[:30] or None
    os.makedirs(INBOX, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9 _.@$#-]", "_", name)
    dest = os.path.join(INBOX, f"{int(time.time()*1000)}_{safe}")
    with open(dest, "wb") as f:
        f.write(data)
    from app.common import producers as P
    producers = P.parse(name, db())
    db().execute("INSERT INTO beats(file_path, original_name, producers, genre) VALUES (%s,%s,%s,%s)",
                  (dest, name, producers, g))
    return {"ok": True}


@app.post("/beats/{bid}/edit")
def beat_edit(bid: int, genre: str = Form(""), bpm: str = Form(""), key: str = Form(""), bs: str = Form("")):
    bs = bs.strip()
    if bs and not re.match(r"^https://(www\.)?beatstars\.com/", bs):
        bs = ""
    db().execute("""UPDATE beats SET genre=nullif(%s,''), bpm=coalesce(nullif(%s,'')::real, bpm),
                    musical_key=coalesce(nullif(%s,''), musical_key), beatstars_url=coalesce(nullif(%s,''), beatstars_url),
                    needs_review=false WHERE id=%s""",
                 (genre.strip().lower(), bpm.strip(), key.strip(), bs, bid))
    return RedirectResponse("/beats", status_code=303)


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request):
    c = db()
    rows = c.execute("""SELECT u.id uid, u.scheduled, u.title, u.title_locked, u.formula, u.is_test, u.status, u.error,
                        b.id bid, b.name, b.genre, b.cover_path FROM uploads u JOIN beats b ON b.id=u.beat_id
                        WHERE u.status IN ('scheduled','final','error') ORDER BY u.scheduled""").fetchall()
    from urllib.parse import quote_plus
    rows = [dict(r, pins=[(a, "https://www.pinterest.com/search/pins/?q=" + quote_plus(a + " aesthetic"))
                          for a in (r["formula"] or "").split(" x ") if a.strip()]) for r in rows]
    done = c.execute("""SELECT u.done, u.title, u.formula, (SELECT views FROM upload_stats s WHERE s.upload_id=u.id
                         ORDER BY measured DESC LIMIT 1) views FROM uploads u WHERE status='uploaded' ORDER BY done DESC LIMIT 50""").fetchall()
    from app.maint.worker import formula_performance
    return page("queue", request, rows=rows, done=done, perf=formula_performance(c))


@app.post("/beats/{bid}/delete")
def beat_delete(bid: int):
    c = db()
    b = c.execute("SELECT * FROM beats WHERE id=%s", (bid,)).fetchone()
    if b:
        live = c.execute("SELECT 1 FROM uploads WHERE beat_id=%s AND status='uploaded'", (bid,)).fetchone()
        c.execute("DELETE FROM uploads WHERE beat_id=%s AND status <> 'uploaded'", (bid,))
        c.execute("UPDATE email_packs SET beat_ids = array_remove(beat_ids, %s) WHERE sent_at IS NULL", (bid,))
        if live:  # keep the record of what's on YouTube, just retire the beat
            c.execute("UPDATE beats SET status='deleted' WHERE id=%s", (bid,))
        else:
            for f in (b.get("file_path"), b.get("video_path"), b.get("cover_path")):
                try:
                    if f and f.startswith("/data/"):
                        os.remove(f)
                except OSError:
                    pass
            used = c.execute("SELECT 1 FROM email_packs WHERE %s = ANY(beat_ids)", (bid,)).fetchone()
            if used:
                c.execute("UPDATE beats SET status='deleted' WHERE id=%s", (bid,))
            else:
                c.execute("DELETE FROM beats WHERE id=%s", (bid,))
        log_event(c, "info", "web", f"beat deleted: {b['name'] or b['original_name']}")
    return RedirectResponse("/beats", status_code=303)


@app.post("/beats/{bid}/cover")
async def beat_cover(bid: int, file: UploadFile = File(...)):
    from app.pipeline import custom_cover
    data = await file.read()
    try:
        custom_cover.save(db(), bid, data)
    except Exception as e:
        log_event(db(), "warn", "web", f"cover for beat {bid} rejected: {e}")
    return RedirectResponse("/queue", status_code=303)


@app.get("/beats/{bid}/cover.jpg")
def beat_cover_img(bid: int):
    r = db().execute("SELECT cover_path FROM beats WHERE id=%s", (bid,)).fetchone()
    if not r or not r["cover_path"] or not os.path.exists(r["cover_path"]):
        return Response(status_code=404)
    return Response(open(r["cover_path"], "rb").read(), media_type="image/jpeg")


@app.post("/queue/{uid}/title")
def queue_title(uid: int, title: str = Form("")):
    t = title.strip()[:100]
    db().execute("UPDATE uploads SET title=nullif(%s,''), title_locked=(%s<>'') WHERE id=%s", (t, t, uid))
    return RedirectResponse("/queue", status_code=303)


@app.post("/queue/{uid}/move")
def queue_move(uid: int, when: str = Form("")):
    try:
        t = dt.datetime.fromisoformat(when).replace(tzinfo=dt.timezone.utc)
        db().execute("UPDATE uploads SET scheduled=%s, status=CASE WHEN title_locked THEN status ELSE 'scheduled' END WHERE id=%s", (t, uid))
    except ValueError:
        pass
    return RedirectResponse("/queue", status_code=303)


@app.post("/queue/{uid}/skip")
def queue_skip(uid: int):
    c = db()
    row = c.execute("UPDATE uploads SET status='skipped' WHERE id=%s RETURNING beat_id", (uid,)).fetchone()
    if row:
        c.execute("UPDATE beats SET status='analyzed' WHERE id=%s", (row["beat_id"],))
        c.execute("DELETE FROM uploads WHERE id=%s", (uid,))
    return RedirectResponse("/queue", status_code=303)


def verdict(r):
    vpd, sup = r["vpd_med"] or 0, r["sup30"] or 0
    d = "High" if vpd >= 300 else "Medium" if vpd >= 40 else "Low"
    c = "Low" if sup <= 10 else "Medium" if sup <= 30 else "High"
    s7 = r.get("sup7") or 0
    # trend: last 7 days vs the 30-day weekly average
    trend = "rising" if sup >= 4 and s7 * 4.3 > sup * 1.4 else "falling" if sup >= 8 and s7 * 4.3 < sup * 0.6 else ""
    if d == "Low":
        v, cls = "LOW DEMAND", "mut"
    elif c == "Low":
        v, cls = "GOOD NICHE", "ok"
    elif d == "High" and c == "High":
        v, cls = "BIG NAME", ""
    elif c == "High":
        v, cls = "CROWDED", "warn"
    else:
        v, cls = "POSSIBLE", ""
    return {**r, "dlabel": d, "clabel": c, "verdict": v, "vclass": cls, "trend": trend}


@app.get("/research", response_class=HTMLResponse)
def research(request: Request, q: str = "", maxsup: str = "", minvpd: str = "", sort: str = "opp", only: str = ""):
    # "best niches": GOOD NICHE first, then POSSIBLE, BIG NAME, CROWDED, LOW DEMAND; inside each by demand/competition
    rank = """CASE WHEN m.vpd_med < 40 THEN 5
                   WHEN m.sup30 <= 10 THEN 1
                   WHEN m.sup30 > 30 AND m.vpd_med >= 300 THEN 3
                   WHEN m.sup30 > 30 THEN 4 ELSE 2 END"""
    order = {"opp": f"{rank}, (m.vpd_med / (m.sup30 + 2)) DESC", "vpd": "m.vpd_med DESC",
             "sup": "m.sup30 ASC, m.vpd_med DESC", "name": "a.name"}.get(sort, f"{rank}, (m.vpd_med / (m.sup30 + 2)) DESC")
    where, args = ["m.rel_n >= 5"], []
    if q:
        where.append("(a.name ILIKE %s OR EXISTS (SELECT 1 FROM artist_tags t WHERE t.artist_id=a.id AND t.tag ILIKE %s AND t.weight>=0.3))")
        args += [f"%{q}%", f"%{q}%"]
    if only == "niche":
        where.append("m.sup30 <= 10 AND m.vpd_med >= 40")
    if maxsup.strip().isdigit():
        where.append("m.sup30 <= %s")
        args.append(int(maxsup))
    try:
        if minvpd.strip():
            where.append("m.vpd_med >= %s")
            args.append(float(minvpd))
    except ValueError:
        pass
    rows = db().execute(f"""
        SELECT a.name, m.*, (SELECT string_agg(tag, ', ' ORDER BY weight DESC) FROM
            (SELECT tag, max(weight) weight FROM artist_tags t WHERE t.artist_id=a.id AND t.tag NOT LIKE '\\_%%' GROUP BY tag ORDER BY 2 DESC LIMIT 4) x) tags
        FROM artists a JOIN LATERAL (SELECT * FROM yt_metrics y WHERE y.artist_id=a.id ORDER BY measured DESC LIMIT 1) m ON true
        WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 200""", args).fetchall()
    rows = [verdict(r) for r in rows]
    return page("research", request, rows=rows, q=q, maxsup=maxsup, minvpd=minvpd, sort=sort, only=only,
                formulas=json.dumps(meta.load_formulas(db()), indent=2))


@app.get("/covers", response_class=HTMLResponse)
def covers_page(request: Request, genre: str = "detroit"):
    c = db()
    genres = [(r["genre"], r["n"]) for r in c.execute("SELECT genre, count(*) n FROM covers WHERE genre <> '__deleted' GROUP BY genre ORDER BY genre").fetchall()]
    rows = c.execute("SELECT id, used FROM covers WHERE genre=%s ORDER BY id DESC", (genre,)).fetchall()
    return page("covers", request, genres=genres, rows=rows, cur=genre)


@app.get("/covers/img/{cid}")
def cover_img(cid: int):
    r = db().execute("SELECT path FROM covers WHERE id=%s", (cid,)).fetchone()
    if not r or not r["path"].startswith("/data/visuals/") or not os.path.exists(r["path"]):
        return Response(status_code=404)
    return Response(open(r["path"], "rb").read(), media_type="image/jpeg")


@app.post("/covers/delete/{cid}")
def cover_delete(cid: int):
    c = db()
    r = c.execute("SELECT path, genre FROM covers WHERE id=%s", (cid,)).fetchone()
    if r:
        try:
            os.remove(r["path"])
        except OSError:
            pass
        # keep the row (marked) so the same photo is never downloaded again
        c.execute("UPDATE covers SET genre='__deleted', path='' WHERE id=%s", (cid,))
    return RedirectResponse(f"/covers?genre={r['genre'] if r else ''}", status_code=303)


MOOD_TAGS = {"dark", "sad", "hard", "chill", "dreamy", "piano", "guitar", "sample", "uptempo", "experimental",
             "energetic", "aggressive", "melodic", "emotional", "happy", "ambient", "underground"}


@app.get("/research/styles", response_class=HTMLResponse)
def research_styles(request: Request):
    c = db()
    rows = c.execute("""
        WITH last AS (SELECT DISTINCT ON (artist_id) artist_id, vpd_med, sup30 FROM yt_metrics
                      WHERE vpd_med > 0 ORDER BY artist_id, measured DESC),
             tg AS (SELECT artist_id, tag FROM artist_tags WHERE weight >= 0.3 AND tag NOT LIKE '\\_%%' GROUP BY 1, 2)
        SELECT tg.tag, count(*) n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY l.vpd_med) vpd,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY l.sup30) sup,
               count(*) FILTER (WHERE l.sup30 <= 10 AND l.vpd_med >= 40) niches,
               (SELECT string_agg(name, ', ') FROM (SELECT a.name FROM tg t2 JOIN last l2 ON l2.artist_id=t2.artist_id
                    JOIN artists a ON a.id=t2.artist_id WHERE t2.tag=tg.tag AND l2.sup30 <= 10 AND l2.vpd_med >= 40
                    ORDER BY l2.vpd_med DESC LIMIT 5) x) best
        FROM tg JOIN last l ON l.artist_id=tg.artist_id GROUP BY tg.tag HAVING count(*) >= 3 ORDER BY 3 DESC""").fetchall()
    scenes = [r for r in rows if r["tag"] not in MOOD_TAGS]
    moods = [r for r in rows if r["tag"] in MOOD_TAGS]
    return page("styles", request, scenes=scenes, moods=moods)


STUDY = "/data/visuals/study"


@app.get("/study", response_class=HTMLResponse)
def study(request: Request):
    rep = open(f"{STUDY}/report.txt").read() if os.path.exists(f"{STUDY}/report.txt") else "No study yet."
    imgs = sorted(f for f in os.listdir(STUDY) if f.endswith(".jpg")) if os.path.isdir(STUDY) else []
    from html import escape
    body = "".join(f'<h3>{escape(f[:-4].replace("_", " "))}</h3><img src="/study/img/{escape(f)}" style="max-width:100%;border-radius:8px">' for f in imgs)
    return HTMLResponse(f"""<html><body style="background:#111;color:#ddd;font-family:sans-serif;padding:20px">
<a href="/" style="color:#cf0">&larr; dashboard</a><h1>Thumbnail study</h1><pre>{escape(rep)}</pre>{body}</body></html>""")


@app.get("/study/img/{name}")
def study_img(name: str):
    p = os.path.join(STUDY, os.path.basename(name))
    if not name.endswith(".jpg") or not os.path.exists(p):
        return Response(status_code=404)
    return Response(open(p, "rb").read(), media_type="image/jpeg")


CONTACT_COND = {"own": "coalesce(category,'own') = 'own'", "found": "category = 'artist'",
                "producers": "category = 'producer'"}


@app.get("/contacts/export")
def contacts_export(list: str = "own", valid: int = 0, fmt: str = "txt"):
    cond = CONTACT_COND.get(list, CONTACT_COND["own"])
    if valid:
        cond += " AND status='valid'"
    rows = db().execute(f"""SELECT email, name, role, tags, worked_with, status, source FROM contacts
                            WHERE {cond} AND NOT unsubscribed ORDER BY id""").fetchall()
    if fmt == "csv":
        import csv, io
        b = io.StringIO()
        w = csv.writer(b)
        w.writerow(["email", "name", "role", "genres", "worked_with", "status", "source"])
        for r in rows:
            w.writerow([r["email"], r["name"] or "", r["role"] or "", " ".join(r["tags"] or []),
                        ", ".join(r["worked_with"] or []), r["status"], r["source"] or ""])
        return Response(b.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="contacts_{list}.csv"'})
    return Response("\n".join(r["email"] for r in rows), media_type="text/plain")


@app.get("/contacts", response_class=HTMLResponse)
def contacts(request: Request, list: str = "own"):
    c = db()
    grp = list if list in ("own", "found", "producers") else "own"
    cond = {"own": "coalesce(category,'own') = 'own'", "found": "category = 'artist'",
            "producers": "category = 'producer'"}[grp]
    stats = c.execute(f"SELECT status, count(*) n FROM contacts WHERE {cond} GROUP BY status ORDER BY 2 DESC").fetchall()
    rows = c.execute(f"SELECT * FROM contacts WHERE {cond} ORDER BY replied DESC, opened_count DESC, id DESC LIMIT 500").fetchall()
    counts = c.execute("""SELECT count(*) FILTER (WHERE coalesce(category,'own')='own') own,
                                 count(*) FILTER (WHERE category='artist') found,
                                 count(*) FILTER (WHERE category='producer') producers FROM contacts""").fetchone()
    pstat = c.execute("""SELECT count(*) n, count(email_checked) checked, count(*) FILTER (WHERE email_found) found
                         FROM producers""").fetchone()
    return page("contacts", request, stats=[(r["status"], r["n"]) for r in stats], rows=rows, grp=grp, counts=counts,
                send_own=S.get(c, "email_send_own"), send_found=S.get(c, "email_send_found"), pstat=pstat)


@app.post("/contacts/import")
def contacts_import(text: str = Form(""), role: str = Form("")):
    from app.email.contacts import import_text
    n = import_text(db(), text, default_role=role.strip() or None, source="own_list")
    log_event(db(), "info", "contacts", f"imported {n} contacts")
    return RedirectResponse("/contacts", status_code=303)


@app.get("/emails", response_class=HTMLResponse)
def emails(request: Request):
    c = db()
    wk = c.execute("""SELECT count(*) sent, count(*) FILTER (WHERE e.status LIKE 'opened%%') opened,
                      count(*) FILTER (WHERE ct.replied) replied, count(*) FILTER (WHERE ct.status='bounced') bounced
                      FROM email_log e JOIN contacts ct ON ct.id=e.contact_id WHERE e.sent_at > date_trunc('week', now())""").fetchone()
    w = dict(wk)
    w["open_rate"] = round(100 * w["opened"] / w["sent"]) if w["sent"] else 0
    week = c.execute("""SELECT e.*, ct.email, ct.name, ct.role, ct.replied FROM email_log e JOIN contacts ct ON ct.id=e.contact_id
                        WHERE e.sent_at > date_trunc('week', now()) ORDER BY e.sent_at DESC""").fetchall()
    hist = c.execute("""SELECT date_trunc('week', sent_at) wk, count(*) sent, count(*) FILTER (WHERE status LIKE 'opened%%') opened
                        FROM email_log GROUP BY 1 ORDER BY 1 DESC LIMIT 12""").fetchall()
    hist = [{**h, "rate": round(100 * h["opened"] / h["sent"]) if h["sent"] else 0} for h in hist]
    packs = c.execute("""SELECT p.id, p.sent_at, (SELECT string_agg(b.name, ', ') FROM beats b WHERE b.id = ANY(p.beat_ids)) beats,
                         (SELECT count(*) FROM email_log e WHERE e.pack_id=p.id) n FROM email_packs p ORDER BY id DESC LIMIT 20""").fetchall()
    last = c.execute("SELECT id FROM email_packs WHERE sent_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    pending = c.execute("""SELECT count(*) n FROM contacts WHERE status='valid' AND NOT unsubscribed
                           AND id NOT IN (SELECT contact_id FROM email_log WHERE pack_id=%s)""",
                        (last["id"] if last else -1,)).fetchone()["n"] if last else 0
    hot = c.execute("""SELECT * FROM contacts WHERE sent_count > 0 ORDER BY replied DESC, opened_count DESC LIMIT 20""").fetchall()
    return page("emails", request, w=w, week=week, hist=hist, packs=packs, pending=pending, hot=hot,
                enabled=S.get(c, "email_enabled"))


@app.get("/events", response_class=HTMLResponse)
def events(request: Request):
    return page("events", request, rows=db().execute("SELECT * FROM events ORDER BY at DESC LIMIT 300").fetchall())


SETTING_KEYS = ["yt_enabled", "tg_channel_enabled", "tg_channel_id", "telegram_channel_url", "instagram_url",
                "uploads_per_day", "upload_hours_utc", "free_tag", "email_enabled", "email_pack_size", "email_daily_limit",
                "email_send_own", "email_send_found"]


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, err: str = ""):
    c = db()
    s = {k: S.get(c, k) for k in SETTING_KEYS}
    return page("settings", request, s=s, saved=saved, err=err, formulas=json.dumps(meta.load_formulas(c), indent=2),
                artist_bpm=json.dumps(S.get(c, "artist_bpm") or meta.DEFAULT_ARTIST_BPM, indent=2),
                subj="\n".join(S.get(c, "email_subjects")), body=S.get(c, "email_body"), sig=S.get(c, "email_signature"))


@app.post("/settings")
async def settings_save(request: Request):
    c = db()
    f = await request.form()
    try:
        formulas = json.loads(f.get("formulas") or "{}")
        assert isinstance(formulas, dict) and all("main" in v for v in formulas.values())
    except Exception:
        return RedirectResponse("/settings?err=Formulas+JSON+is+invalid", status_code=303)
    try:
        abpm = json.loads(f.get("artist_bpm") or "{}")
        assert all(isinstance(v, list) and len(v) == 2 for v in abpm.values())
    except Exception:
        return RedirectResponse("/settings?err=Artist+BPM+JSON+is+invalid", status_code=303)
    S.set_(c, "artist_bpm", abpm)
    lines = [l.strip()[:120] for l in (f.get("email_subjects") or "").splitlines() if l.strip()]
    if lines:
        S.set_(c, "email_subjects", lines)
    for k in ("email_body", "email_signature"):
        v = (f.get(k) or "").replace("\r", "").strip()
        if v:
            S.set_(c, k, v[:2000])
    hours = [int(h) for h in re.findall(r"\d+", f.get("upload_hours_utc", "")) if 0 <= int(h) <= 23] or [16]
    S.set_(c, "yt_enabled", "yt_enabled" in f)
    S.set_(c, "tg_channel_enabled", "tg_channel_enabled" in f)
    S.set_(c, "email_enabled", "email_enabled" in f)
    S.set_(c, "email_send_own", "email_send_own" in f)
    S.set_(c, "email_send_found", "email_send_found" in f)
    S.set_(c, "tg_channel_id", (f.get("tg_channel_id") or "").strip())
    S.set_(c, "telegram_channel_url", (f.get("telegram_channel_url") or "").strip())
    S.set_(c, "instagram_url", (f.get("instagram_url") or "").strip())
    S.set_(c, "uploads_per_day", max(1, min(6, int(f.get("uploads_per_day") or 1))))
    S.set_(c, "upload_hours_utc", hours)
    S.set_(c, "free_tag", f.get("free_tag") if f.get("free_tag") in ("on", "off", "test") else "test")
    S.set_(c, "email_pack_size", max(1, min(6, int(f.get("email_pack_size") or 5))))
    S.set_(c, "email_daily_limit", max(1, min(400, int(f.get("email_daily_limit") or 40))))
    S.set_(c, "formulas", formulas)
    log_event(c, "info", "settings", "settings updated from dashboard")
    return RedirectResponse("/settings?saved=1", status_code=303)
