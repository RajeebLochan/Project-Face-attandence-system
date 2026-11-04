#!/bin/sh
# Restore a backup made by deploy/backup.sh into the running Compose deployment.
# Usage: deploy/restore.sh /path/db-STAMP.dump /path/private-STAMP.tar.gz
# Replaces the current database contents and photo store. Restore both files from the
# SAME backup, then re-apply deletions recorded after that backup (see docs/OPERATIONS.md).
set -eu

db_dump="${1:?usage: deploy/restore.sh db.dump private.tar.gz}"
private_tar="${2:?usage: deploy/restore.sh db.dump private.tar.gz}"
[ -f "$db_dump" ] && [ -f "$private_tar" ] || { echo "Backup files not found." >&2; exit 1; }

echo "Stopping application containers (the database keeps running)..."
docker compose stop web worker scheduler proxy

docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction' \
  < "$db_dump"

docker compose run --rm -T --no-deps --entrypoint sh web -c \
  'find /data/private_media -mindepth 1 -delete && tar -C /data -xzf -' < "$private_tar"

docker compose run --rm --no-deps web migrate
docker compose up -d
echo "Restore finished. Check the Today page and run: docker compose run --rm web manage attendance_preflight"
