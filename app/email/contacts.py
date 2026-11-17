"""Contacts: import from any text, verify (syntax, domain MX, disposable), status tracking."""
import re
import dns.resolver
import dns.exception

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
DISPOSABLE = {"mailinator.com", "10minutemail.com", "guerrillamail.com", "tempmail.com", "yopmail.com",
              "trashmail.com", "getnada.com", "sharklasers.com", "dispostable.com", "temp-mail.org"}
ROLES = {"artist", "a&r", "ar", "manager", "producer", "label", "engineer"}


def import_text(conn, text: str, default_role=None, source="own_list") -> int:
    n = 0
    for line in text.splitlines():
        emails = EMAIL_RE.findall(line)
        if not emails:
            continue
        rest = [p.strip(" \"'") for p in re.split(r"[,;\t|]", EMAIL_RE.sub("", line)) if p.strip(" \"'")]
        role = next((p.lower() for p in rest if p.lower() in ROLES), default_role)
        name = next((p for p in rest if p.lower() not in ROLES), None)
        for e in emails:
            e = e.strip(".").lower()
            r = conn.execute("""INSERT INTO contacts(email, name, role, source) VALUES (%s,%s,%s,%s)
                                ON CONFLICT (email) DO NOTHING RETURNING id""", (e, name, role, source)).fetchone()
            n += 1 if r else 0
    return n


_mx_cache: dict[str, bool] = {}


def domain_ok(domain: str) -> bool:
    if domain in _mx_cache:
        return _mx_cache[domain]
    ok = False
    try:
        ok = len(dns.resolver.resolve(domain, "MX", lifetime=8)) > 0
    except dns.resolver.NoAnswer:
        try:
            ok = len(dns.resolver.resolve(domain, "A", lifetime=8)) > 0
        except dns.exception.DNSException:
            ok = False
    except dns.exception.DNSException:
        ok = False
    _mx_cache[domain] = ok
    return ok


def verify(email: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email):
        return "invalid"
    domain = email.split("@", 1)[1].lower()
    if domain in DISPOSABLE:
        return "risky"
    return "valid" if domain_ok(domain) else "invalid"


def verify_pending(conn, limit=200) -> int:
    rows = conn.execute("""SELECT id, email FROM contacts WHERE status IN ('unverified')
                           OR (status IN ('valid','unknown') AND (last_checked IS NULL OR last_checked < now()-interval '90 days'))
                           ORDER BY id LIMIT %s""", (limit,)).fetchall()
    for r in rows:
        conn.execute("UPDATE contacts SET status=%s, last_checked=now() WHERE id=%s", (verify(r["email"]), r["id"]))
    return len(rows)
