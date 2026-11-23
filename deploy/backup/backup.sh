#!/bin/sh
# Nightly database backup (plan §10): pg_dump -Fc, verified, kept 14 daily / 8 weekly / 12 monthly.
# Off-site copy with rclone when RCLONE_REMOTE is set (e.g. "b2:tradesphere-backups"); the remote
# is configured through RCLONE_CONFIG_<NAME>_* environment variables in deploy/.env.
# Connection settings come from PGHOST, PGUSER, PGPASSWORD, PGDATABASE.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
WEEKDAY="$(date -u +%u)" # 7 = Sunday
DAY_OF_MONTH="$(date -u +%d)"

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly" "$BACKUP_DIR/monthly"
FILE="$BACKUP_DIR/daily/tradesphere-$STAMP.dump"

# Write to a temporary name, check the archive can be read, and only then publish it.
pg_dump --format=custom --compress=9 --file="$FILE.part" "$PGDATABASE"
pg_restore --list "$FILE.part" >/dev/null
mv "$FILE.part" "$FILE"

if [ "$WEEKDAY" = "7" ]; then cp "$FILE" "$BACKUP_DIR/weekly/"; fi
if [ "$DAY_OF_MONTH" = "01" ]; then cp "$FILE" "$BACKUP_DIR/monthly/"; fi

# Keep the newest N dumps in each folder.
prune() {
	folder="$1"
	keep="$2"
	ls -1t "$folder"/*.dump 2>/dev/null | tail -n "+$((keep + 1))" | while read -r old; do
		rm -f -- "$old"
	done
}
prune "$BACKUP_DIR/daily" 14
prune "$BACKUP_DIR/weekly" 8
prune "$BACKUP_DIR/monthly" 12

# Off-site copy. `copy`, never `sync`: a fresh or emptied server must not delete the remote history.
if [ -n "${RCLONE_REMOTE:-}" ]; then
	rclone copy "$BACKUP_DIR" "$RCLONE_REMOTE" --transfers 2
	echo "off-site copy to $RCLONE_REMOTE done"
fi

echo "backup $FILE ok"
