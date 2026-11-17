"""Set dashboard password + 2FA (run on the server):
  docker compose run --rm -v "$PWD/deploy:/srv/deploy" web python deploy/setup_dashboard.py
"""
import getpass
import pyotp
import qrcode
from argon2 import PasswordHasher
from app.common.db import connect, ensure_schema
from app.common import settings as S

conn = connect()
ensure_schema(conn)
while True:
    p1 = getpass.getpass("New dashboard password (min 12 chars): ")
    p2 = getpass.getpass("Repeat: ")
    if p1 == p2 and len(p1) >= 12:
        break
    print("Passwords don't match or too short.")
secret = pyotp.random_base32()
old = S.get(conn, "auth") or {}
S.set_(conn, "auth", {"pw": PasswordHasher().hash(p1), "totp": secret, "ver": old.get("ver", 0) + 1})
uri = pyotp.TOTP(secret).provisioning_uri(name="owner", issuer_name="Beat Manager")
print("\nScan this QR code with Google Authenticator / Authy / 1Password:\n")
q = qrcode.QRCode(border=1)
q.add_data(uri)
q.print_ascii(invert=True)
print("\nIf the QR doesn't scan, add this key manually:", secret)
print("Done. All old sessions are logged out.")
