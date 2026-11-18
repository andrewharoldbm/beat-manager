"""One-time Google login on the server.
  YouTube (channel account):  docker compose run --rm -v "$PWD/deploy:/srv/deploy" scheduler python deploy/auth_google.py youtube
  Gmail (loops4conkre):       docker compose run --rm -v "$PWD/deploy:/srv/deploy" scheduler python deploy/auth_google.py gmail
1. Open the printed link in your browser, log in with the RIGHT Google account, approve.
2. The browser then shows an error page at "localhost" - that's expected.
3. Copy the FULL address from the browser bar and paste it here.
"""
import sys
sys.path.insert(0, "/srv")
import os
import sys
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
from google_auth_oauthlib.flow import Flow
which = (sys.argv[1] if len(sys.argv) > 1 else "youtube").lower()
if which == "gmail":
    from app.email.gmail import CLIENT, TOKEN, SCOPES
else:
    from app.pipeline.youtube_api import CLIENT, TOKEN, SCOPES
if not os.path.exists(CLIENT):
    raise SystemExit(f"Missing {CLIENT}. Copy client_secret.json into the secrets volume first.")
flow = Flow.from_client_secrets_file(CLIENT, scopes=SCOPES, redirect_uri="http://localhost:8765/")
url, _ = flow.authorization_url(access_type="offline", prompt="consent")
print(f"\n[{which}] Open this link in your browser:\n\n{url}\n")
resp = input("Paste the full localhost address here: ").strip()
flow.fetch_token(authorization_response=resp)
with open(TOKEN, "w") as f:
    f.write(flow.credentials.to_json())
os.chmod(TOKEN, 0o600)
print(f"Saved {which} login.")
try:
    from googleapiclient.discovery import build
    if which == "youtube":
        ch = build("youtube", "v3", credentials=flow.credentials, cache_discovery=False).channels().list(part="snippet", mine=True).execute()
        print("Connected YouTube channel:", ", ".join(i["snippet"]["title"] for i in ch.get("items", [])) or "NONE")
    else:
        p = build("gmail", "v1", credentials=flow.credentials, cache_discovery=False).users().getProfile(userId="me").execute()
        print("Connected Gmail:", p.get("emailAddress"))
except Exception as e:
    print("Could not check account:", e)
