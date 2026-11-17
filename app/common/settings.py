"""Runtime settings stored in the DB (editable from dashboard), with env fallbacks."""
import json
import os

DEFAULTS = {
    "yt_enabled": os.environ.get("YT_ENABLED", "0") == "1",
    "tg_channel_enabled": os.environ.get("TG_CHANNEL_ENABLED", "0") == "1",
    "tg_channel_id": os.environ.get("TG_CHANNEL_ID", ""),
    "telegram_channel_url": os.environ.get("TELEGRAM_CHANNEL_URL", ""),
    "instagram_url": os.environ.get("INSTAGRAM_URL", "instagram.com/conkretus"),
    "uploads_per_day": int(os.environ.get("UPLOADS_PER_DAY", "1")),
    "upload_hours_utc": [int(h) for h in os.environ.get("UPLOAD_HOURS_UTC", "16,22").split(",")],
    "free_tag": os.environ.get("TITLE_FREE_TAG", "test"),
    "email_enabled": False,
    "email_pack_size": 5,
    "email_weekday": 1,          # Tuesday (0=Mon)
    "email_daily_limit": 40,
    "autopilot_hours": 72,
    "artist_bpm": None,
    # drafts - to be reviewed with the owner before emails are switched on
    "email_subjects": ["fresh stuff", "fresh beats"],
    "email_body": "yoo, sending you some fresh stuff",
    "email_signature": "conkretus\n\nall beats are tagged. leases $60 (mp3), credit: prod. by conkretus.\nfor untagged files or exclusives just reply to this email.\nreply \"stop\" and I won't send more.",
}


def get(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
    if row is not None and row["value"] is not None:
        return row["value"]
    return DEFAULTS.get(key)


def set_(conn, key, value):
    conn.execute("""INSERT INTO settings(key,value) VALUES (%s,%s::jsonb)
                    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", (key, json.dumps(value)))
