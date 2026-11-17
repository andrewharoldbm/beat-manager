#!/bin/sh
# Daily compressed DB backup with retention: 7 daily, 4 weekly (Sunday), 3 monthly (1st).
set -e
mkdir -p /backups/daily /backups/weekly /backups/monthly
while true; do
  D=$(date +%Y-%m-%d)
  F=/backups/daily/beats-$D.sql.gz
  if [ ! -f "$F" ]; then
    pg_dump -h db -U beats beats | gzip -9 > "$F.tmp" && mv "$F.tmp" "$F"
    [ "$(date +%u)" = "7" ] && cp "$F" /backups/weekly/
    [ "$(date +%d)" = "01" ] && cp "$F" /backups/monthly/
    ls -1t /backups/daily/*.sql.gz | tail -n +8 | xargs -r rm -f
    ls -1t /backups/weekly/*.sql.gz 2>/dev/null | tail -n +5 | xargs -r rm -f
    ls -1t /backups/monthly/*.sql.gz 2>/dev/null | tail -n +4 | xargs -r rm -f
    echo "backup done $F"
  fi
  sleep 3600
done
