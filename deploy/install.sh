#!/usr/bin/env bash
# Beat Manager bootstrap installer (Ubuntu 24.04). Contains no secrets.
set -euo pipefail
REPO="git@github.com:andrewharoldbm/beat-manager.git"
DIR=/opt/beat-manager

echo "== 1/6 System updates & security =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get upgrade -y
apt-get install -y ufw fail2ban unattended-upgrades git curl ca-certificates openssl
dpkg-reconfigure -f noninteractive unattended-upgrades
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw --force enable
systemctl enable --now fail2ban

echo "== 2/6 Docker =="
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi

echo "== 3/6 Deploy key for private code repo =="
mkdir -p /root/.ssh && chmod 700 /root/.ssh
[ -f /root/.ssh/bm_deploy ] || ssh-keygen -t ed25519 -N "" -f /root/.ssh/bm_deploy -C "beat-manager-server" >/dev/null
cat > /root/.ssh/config <<EOF
Host github.com
  IdentityFile /root/.ssh/bm_deploy
  StrictHostKeyChecking accept-new
EOF
echo
echo "############ COPY THIS LINE AND SEND IT TO CLAUDE ############"
cat /root/.ssh/bm_deploy.pub
echo "##############################################################"
echo
read -rp "Press ENTER after Claude confirms the key was added... " _

echo "== 4/6 Fetch code =="
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull; else git clone "$REPO" "$DIR"; fi
cd "$DIR"

echo "== 5/6 Config =="
if [ ! -f .env ]; then
  DBP=$(openssl rand -hex 24)
  cat > .env <<EOF
DB_PASSWORD=$DBP
LASTFM_API_KEY=
YT_API_KEY=
TELEGRAM_BOT_TOKEN=
OWNER_CHAT_ID=
KILL_PIN_SHA256=
EOF
  chmod 600 .env
fi
mkdir -p data/beats/inbox data/postgres && chown -R 1000:1000 data/beats

echo "== 6/6 Start =="
docker compose up -d --build
# auto-update from GitHub every 30 min
cat > /etc/cron.d/beat-manager <<EOF
*/30 * * * * root cd $DIR && git pull -q && docker compose up -d --build >/dev/null 2>&1
EOF
echo "DONE. Services:"; docker compose ps
