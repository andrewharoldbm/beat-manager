"""Dashboard auth: one owner account, password (argon2) + TOTP 2FA, signed session cookie."""
import os
import time
import pyotp
from argon2 import PasswordHasher
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.common import settings as S

ph = PasswordHasher()
SECRET = os.environ.get("WEB_SECRET") or ""
SESSION_HOURS = 12
_attempts: dict[str, list[float]] = {}


def serializer(conn):
    key = SECRET or S.get(conn, "web_secret")
    if not key:
        key = os.urandom(32).hex()
        S.set_(conn, "web_secret", key)
    return URLSafeTimedSerializer(key, salt="bm-session")


def configured(conn) -> bool:
    a = S.get(conn, "auth")
    return bool(a and a.get("pw") and a.get("totp"))


def rate_limited(ip: str) -> bool:
    now = time.time()
    arr = [t for t in _attempts.get(ip, []) if now - t < 900]
    _attempts[ip] = arr
    return len(arr) >= 5


def check_login(conn, ip, password, code) -> bool:
    if rate_limited(ip):
        return False
    a = S.get(conn, "auth") or {}
    ok = False
    try:
        ok = ph.verify(a.get("pw", ""), password) and pyotp.TOTP(a.get("totp", "")).verify(code, valid_window=1)
    except Exception:
        ok = False
    if not ok:
        _attempts.setdefault(ip, []).append(time.time())
    return ok


def make_session(conn):
    return serializer(conn).dumps({"u": "owner", "v": (S.get(conn, "auth") or {}).get("ver", 1)})


def valid_session(conn, token) -> bool:
    if not token:
        return False
    try:
        data = serializer(conn).loads(token, max_age=SESSION_HOURS * 3600)
        return data.get("v") == (S.get(conn, "auth") or {}).get("ver", 1)
    except (BadSignature, SignatureExpired):
        return False
