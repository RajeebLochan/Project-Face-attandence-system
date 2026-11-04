#!/bin/sh
# Back up the database and the private photo store of the Compose deployment.
# Usage: deploy/backup.sh /secure/backup/directory
# The output contains personal and biometric data: keep it encrypted and access-controlled,
# and delete it when the institution's retention period ends.
set -eu

dest="${1:?usage: deploy/backup.sh /secure/backup/directory}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
mkdir -p "$dest"

docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$dest/db-$stamp.dump"
docker compose run --rm -T --no-deps --entrypoint tar web -C /data -czf - private_media \
  > "$dest/private-$stamp.tar.gz"

(cd "$dest" && sha256sum "db-$stamp.dump" "private-$stamp.tar.gz" > "SHA256SUMS-$stamp")
echo "Backup written to $dest (db-$stamp.dump, private-$stamp.tar.gz, SHA256SUMS-$stamp)"
