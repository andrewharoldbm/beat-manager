"""Gmail API (sending account: loops4conkre). OAuth token stored in secrets volume."""
import base64
import os
from email.message import EmailMessage
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SECRETS = os.environ.get("SECRETS_DIR", "/data/secrets")
CLIENT = os.path.join(SECRETS, "client_secret.json")
TOKEN = os.path.join(SECRETS, "gmail_token.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]


def service():
    if not os.path.exists(TOKEN):
        return None
    c = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if c.expired and c.refresh_token:
        c.refresh(Request())
        with open(TOKEN, "w") as f:
            f.write(c.to_json())
    return build("gmail", "v1", credentials=c, cache_discovery=False)


def send(svc, to, subject, text, html=None, attachments=()):
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for path, name in attachments:
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype="audio", subtype="mpeg", filename=name)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return svc.users().messages().send(userId="me", body={"raw": raw}).execute()
