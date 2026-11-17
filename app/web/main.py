"""Beat Manager web dashboard (owner only, password + 2FA)."""
import datetime as dt
import json
import os
import re
import time
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
    if path not in ("/login", "/health") and not auth.valid_session(db(), request.cookies.get(COOKIE)):
        if path.startswith("/api/"):
            return JSONResponse({"error": "auth"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:"
    return resp


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


@app.get("/beats", response_class=HTMLResponse)
def beats(request: Request):
    return page("beats", request, beats=db().execute("SELECT * FROM beats ORDER BY id DESC LIMIT 500").fetchall())


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
    producers = re.findall(r"@([A-Za-z0-9_$-]+)", name) or ["conkretus"]
    db().execute("INSERT INTO beats(file_path, original_name, producers, genre) VALUES (%s,%s,%s,%s)",
                  (dest, name, producers, g))
    return {"ok": True}


@app.post("/beats/{bid}/edit")
def beat_edit(bid: int, genre: str = Form(""), bpm: str = Form(""), key: str = Form("")):
    db().execute("""UPDATE beats SET genre=nullif(%s,''), bpm=coalesce(nullif(%s,'')::real, bpm),
                    musical_key=coalesce(nullif(%s,''), musical_key), needs_review=false WHERE id=%s""",
                 (genre.strip().lower(), bpm.strip(), key.strip(), bid))
    return RedirectResponse("/beats", status_code=303)


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request):
    c = db()
    rows = c.execute("""SELECT u.id uid, u.scheduled, u.title, u.title_locked, u.formula, u.is_test, u.status, u.error,
                        b.name, b.genre FROM uploads u JOIN beats b ON b.id=u.beat_id
                        WHERE u.status IN ('scheduled','final','error') ORDER BY u.scheduled""").fetchall()
    done = c.execute("SELECT done, title, formula FROM uploads WHERE status='uploaded' ORDER BY done DESC LIMIT 50").fetchall()
    return page("queue", request, rows=rows, done=done)


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


@app.get("/research", response_class=HTMLResponse)
def research(request: Request, q: str = "", maxsup: str = "", minvpd: str = "", sort: str = "opp"):
    order = {"opp": "(m.vpd_med / (m.sup30 + 2)) DESC", "vpd": "m.vpd_med DESC", "sup": "m.sup30 ASC, m.vpd_med DESC",
             "name": "a.name"}.get(sort, "(m.vpd_med / (m.sup30 + 2)) DESC")
    where, args = ["m.rel_n >= 5"], []
    if q:
        where.append("(a.name ILIKE %s OR EXISTS (SELECT 1 FROM artist_tags t WHERE t.artist_id=a.id AND t.tag ILIKE %s AND t.weight>=0.3))")
        args += [f"%{q}%", f"%{q}%"]
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
            (SELECT tag, max(weight) weight FROM artist_tags t WHERE t.artist_id=a.id GROUP BY tag ORDER BY 2 DESC LIMIT 4) x) tags
        FROM artists a JOIN LATERAL (SELECT * FROM yt_metrics y WHERE y.artist_id=a.id ORDER BY measured DESC LIMIT 1) m ON true
        WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 200""", args).fetchall()
    return page("research", request, rows=rows, q=q, maxsup=maxsup, minvpd=minvpd, sort=sort,
                formulas=json.dumps(meta.load_formulas(db()), indent=2))


@app.get("/contacts", response_class=HTMLResponse)
def contacts(request: Request):
    c = db()
    stats = c.execute("SELECT status, count(*) n FROM contacts GROUP BY status ORDER BY 2 DESC").fetchall()
    rows = c.execute("SELECT * FROM contacts ORDER BY replied DESC, opened_count DESC, id DESC LIMIT 500").fetchall()
    return page("contacts", request, stats=[(r["status"], r["n"]) for r in stats], rows=rows)


@app.post("/contacts/import")
def contacts_import(text: str = Form(""), role: str = Form("")):
    from app.email.contacts import import_text
    n = import_text(db(), text, default_role=role.strip() or None, source="own_list")
    log_event(db(), "info", "contacts", f"imported {n} contacts")
    return RedirectResponse("/contacts", status_code=303)


@app.get("/events", response_class=HTMLResponse)
def events(request: Request):
    return page("events", request, rows=db().execute("SELECT * FROM events ORDER BY at DESC LIMIT 300").fetchall())


SETTING_KEYS = ["yt_enabled", "tg_channel_enabled", "tg_channel_id", "telegram_channel_url", "instagram_url",
                "uploads_per_day", "upload_hours_utc", "free_tag", "email_enabled", "email_pack_size", "email_daily_limit"]


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, err: str = ""):
    c = db()
    s = {k: S.get(c, k) for k in SETTING_KEYS}
    return page("settings", request, s=s, saved=saved, err=err, formulas=json.dumps(meta.load_formulas(c), indent=2))


@app.post("/settings")
async def settings_save(request: Request):
    c = db()
    f = await request.form()
    try:
        formulas = json.loads(f.get("formulas") or "{}")
        assert isinstance(formulas, dict) and all("main" in v for v in formulas.values())
    except Exception:
        return RedirectResponse("/settings?err=Formulas+JSON+is+invalid", status_code=303)
    hours = [int(h) for h in re.findall(r"\d+", f.get("upload_hours_utc", "")) if 0 <= int(h) <= 23] or [16]
    S.set_(c, "yt_enabled", "yt_enabled" in f)
    S.set_(c, "tg_channel_enabled", "tg_channel_enabled" in f)
    S.set_(c, "email_enabled", "email_enabled" in f)
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
