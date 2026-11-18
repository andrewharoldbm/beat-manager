#!/bin/bash
# Saves the Genius API token on the server (typed, not shown) and restarts the worker.
set -e
cd "$(dirname "$0")/.."
read -rsp "Paste Genius Client Access Token: " T < /dev/tty; echo
[ -n "$T" ] || { echo "empty, nothing saved"; exit 1; }
sed -i '/^GENIUS_TOKEN=/d' .env
echo "GENIUS_TOKEN=$T" >> .env
chmod 600 .env
docker compose up -d maint >/dev/null
echo "Saved. Genius is now used for producer credits."
