"""YouTube Data API upload + OAuth (headless-friendly)."""
import json
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SECRETS = os.environ.get("SECRETS_DIR", "/data/secrets")
CLIENT = os.path.join(SECRETS, "client_secret.json")
TOKEN = os.path.join(SECRETS, "yt_token.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]


def creds():
    if not os.path.exists(TOKEN):
        return None
    c = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if c and c.expired and c.refresh_token:
        c.refresh(Request())
        with open(TOKEN, "w") as f:
            f.write(c.to_json())
    return c


def service():
    c = creds()
    return build("youtube", "v3", credentials=c, cache_discovery=False) if c else None


def upload(video_path, title, description, tags, thumbnail=None, publish_at=None):
    yt = service()
    if not yt:
        raise RuntimeError("YouTube not connected (run deploy/auth_youtube.py)")
    status = {"privacyStatus": "private" if publish_at else "public", "selfDeclaredMadeForKids": False}
    if publish_at:
        status["publishAt"] = publish_at  # RFC3339
    body = {"snippet": {"title": title, "description": description, "tags": tags, "categoryId": "10"},
            "status": status}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    if thumbnail:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumbnail)).execute()
        except Exception:
            pass
    return vid
