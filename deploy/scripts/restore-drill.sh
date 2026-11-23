#!/usr/bin/env bash
# Restore drill (plan §10, monthly): restore a backup into a scratch database, verify it with the
# reconcile checks and row counts, then drop the scratch database. Production data is not touched.
#
#   ./scripts/restore-drill.sh                 # newest daily dump
#   ./scripts/restore-drill.sh backups/monthly/tradesphere-2026-11-01T0200Z.dump
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT="tradesphere-prod"
COMPOSE=(docker compose -p "$PROJECT" -f docker-compose.prod.yml --env-file .env)

set -a
# shellcheck disable=SC1091
. ./.env
set +a

DUMP="${1:-$(ls -1t backups/daily/*.dump | head -n 1)}"
if [[ ! -f "$DUMP" ]]; then
	echo "no backup file found: $DUMP" >&2
	exit 1
fi
DRILL_DB="drill_$(date -u +%Y%m%d%H%M%S)"
started=$(date +%s)

echo "restore drill: $DUMP -> scratch database $DRILL_DB"
"${COMPOSE[@]}" cp "$DUMP" db:/tmp/drill.dump

cleanup() {
	"${COMPOSE[@]}" exec -T db sh -c "rm -f /tmp/drill.dump; dropdb -U \"\$POSTGRES_USER\" --if-exists $DRILL_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${COMPOSE[@]}" exec -T db sh -c "createdb -U \"\$POSTGRES_USER\" $DRILL_DB && pg_restore --no-owner --no-privileges -U \"\$POSTGRES_USER\" -d $DRILL_DB /tmp/drill.dump"

echo "reconcile (stock, cost, inventory account, trial balance, daily projection):"
"${COMPOSE[@]}" run --rm -T \
	-e "DATABASE_URL=postgresql+psycopg://${POSTGRES_USER:-tradesphere}:${POSTGRES_PASSWORD}@db:5432/$DRILL_DB" \
	migrate python -m app.cli reconcile

echo "row counts:"
"${COMPOSE[@]}" exec -T db psql -U "${POSTGRES_USER:-tradesphere}" -d "$DRILL_DB" -At -c \
	"select 'users', count(*) from users union all select 'sales_invoices', count(*) from sales_invoices union all select 'journal_entries', count(*) from journal_entries union all select 'audit_logs', count(*) from audit_logs;"

elapsed=$(( $(date +%s) - started ))
echo "restore drill PASSED in ${elapsed}s"
