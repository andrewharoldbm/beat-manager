"""One-time YouTube login (run on the server):
  docker compose run --rm -v "$PWD/deploy:/srv/deploy" scheduler python deploy/auth_youtube.py
1. Open the printed link in your browser and log in with the YouTube channel's Google account.
2. After approving, the browser shows an error page at "localhost" - that's expected.
3. Copy the FULL address from the browser bar and paste it here.
"""
import os
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
from google_auth_oauthlib.flow import Flow
from app.pipeline.youtube_api import CLIENT, TOKEN, SCOPES

flow = Flow.from_client_secrets_file(CLIENT, scopes=SCOPES, redirect_uri="http://localhost:8765/")
url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
print("\nOpen this link in your browser:\n\n" + url + "\n")
resp = input("Paste the full localhost address here: ").strip()
flow.fetch_token(authorization_response=resp)
os.makedirs(os.path.dirname(TOKEN), exist_ok=True)
with open(TOKEN, "w") as f:
    f.write(flow.credentials.to_json())
os.chmod(TOKEN, 0o600)
from googleapiclient.discovery import build
ch = build("youtube", "v3", credentials=flow.credentials).channels().list(part="snippet", mine=True).execute()
print("Connected to channel:", ch["items"][0]["snippet"]["title"] if ch.get("items") else "(no channel found)")
