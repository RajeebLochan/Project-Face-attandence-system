#!/bin/sh
# End-to-end check of the Compose deployment with synthetic data and a local mail catcher.
# Usage: deploy/smoke-test.sh [model-dir]
#   model-dir (optional): directory with the two verified ONNX files; without it the models
#   are downloaded with checksum verification.
# Leaves nothing behind: the stack and its volumes are removed at the end.
set -eu

project="attendance-smoke"
workdir="$(mktemp -d)"
env_file="$workdir/smoke.env"
admin_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
db_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
secret_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"

cat > "$env_file" <<EOF
SITE_ADDRESS=localhost
SITE_URL=https://localhost
DJANGO_ALLOWED_HOSTS=localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost
DJANGO_TRUST_X_FORWARDED_PROTO=true
DJANGO_SECRET_KEY=$secret_key
POSTGRES_PASSWORD=$db_password
DATABASE_URL=postgres://attendance:$db_password@db:5432/attendance
EMAIL_HOST=mailpit
EMAIL_PORT=1025
EMAIL_USE_TLS=false
DEFAULT_FROM_EMAIL=Attendance <attendance@localhost>
GUNICORN_WORKERS=3
NOTIFICATION_WORKER_POLL_SECONDS=2
EOF

# The backup/restore scripts call plain `docker compose`, so configure it via the environment.
export ENV_FILE="$env_file" COMPOSE_PROJECT_NAME="$project" COMPOSE_PROFILES=mailtest COMPOSE_ENV_FILES="$env_file"
compose() { docker compose "$@"; }
# KEEP_STACK=1 leaves the containers running for inspection (remove with: docker compose down -v).
cleanup() { [ "${KEEP_STACK:-}" = "1" ] || compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
fail() { echo "SMOKE TEST FAILED: $*" >&2; compose logs --tail 40 >&2 || true; exit 1; }
https() { curl -sk --resolve localhost:443:127.0.0.1 "$@"; }

echo "== start database and mail catcher"
compose up -d db mailpit
echo "== migrate (and create the cache table)"
compose run --rm web migrate >/dev/null
echo "== install face models with checksum verification"
if [ "${1:-}" != "" ]; then
  compose run --rm -v "$1:/models:ro" web setup-models --from-dir /models >/dev/null
else
  compose run --rm web setup-models >/dev/null
fi
echo "== create the administrator account"
compose run --rm -e DJANGO_SUPERUSER_PASSWORD="$admin_password" web manage createsuperuser \
  --noinput --username smoke-admin --email admin@localhost >/dev/null
echo "== start the full stack"
compose up -d
for i in $(seq 1 60); do
  [ "$(https -o /dev/null -w '%{http_code}' https://localhost/healthz)" = "200" ] && break
  sleep 2
done

echo "== HTTPS, headers and private media"
[ "$(https https://localhost/readyz)" = '{"status": "ready"}' ] || fail "readyz"
code=$(curl -s -o /dev/null -w '%{http_code}' --resolve localhost:80:127.0.0.1 http://localhost/login/)
[ "$code" = "308" ] || [ "$code" = "301" ] || fail "HTTP did not redirect to HTTPS ($code)"
headers=$(https -D - -o /dev/null https://localhost/login/)
echo "$headers" | grep -qi '^strict-transport-security: max-age=31536000' || fail "HSTS missing"
echo "$headers" | grep -qi '^content-security-policy:' || fail "CSP missing"
echo "$headers" | grep -i '^set-cookie: csrftoken' | grep -qi 'secure' || fail "CSRF cookie not secure"
[ "$(https -o /dev/null -w '%{http_code}' https://localhost/static/app1/app.css)" = "200" ] || fail "static"
for path in /media/students/x.jpg /data/private_media/ /private_media/ /students/1/photo/; do
  code=$(https -o /dev/null -w '%{http_code}' "https://localhost$path")
  case "$code" in 404|302) ;; *) fail "$path returned $code" ;; esac
done
# Unknown host names never reach the app through the proxy, and the app itself rejects them.
https -H 'Host: evil.example' https://localhost/login/ | grep -q 'csrfmiddlewaretoken' && fail "proxy served unknown host"
direct=$(compose exec -T web python -c "
import urllib.request, urllib.error
r = urllib.request.Request('http://127.0.0.1:8000/login/', headers={'Host': 'evil.example', 'X-Forwarded-Proto': 'https'})
try:
    print(urllib.request.urlopen(r, timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)")
[ "$direct" = "400" ] || fail "app accepted an unknown host ($direct)"

echo "== login through the proxy (CSRF origin and secure cookie checks)"
jar="$workdir/cookies"
token=$(https -c "$jar" https://localhost/login/ | sed -n 's/.*name="csrfmiddlewaretoken" value="\([^"]*\)".*/\1/p' | head -1)
code=$(https -b "$jar" -c "$jar" -o /dev/null -w '%{http_code}' -H 'Referer: https://localhost/login/' \
  --data-urlencode "csrfmiddlewaretoken=$token" --data-urlencode "username=smoke-admin" \
  --data-urlencode "password=$admin_password" https://localhost/login/)
[ "$code" = "302" ] || fail "login returned $code"
[ "$(https -b "$jar" -o /dev/null -w '%{http_code}' https://localhost/settings/)" = "200" ] || fail "settings"

echo "== login throttling holds across gunicorn workers (shared database cache)"
for i in 1 2 3 4 5; do
  t=$(https -c "$workdir/c2" https://localhost/login/ | sed -n 's/.*name="csrfmiddlewaretoken" value="\([^"]*\)".*/\1/p' | head -1)
  https -b "$workdir/c2" -c "$workdir/c2" -o /dev/null -H 'Referer: https://localhost/login/' \
    --data-urlencode "csrfmiddlewaretoken=$t" --data-urlencode "username=smoke-admin" \
    --data-urlencode "password=wrong-$i" https://localhost/login/
done
t=$(https -c "$workdir/c2" https://localhost/login/ | sed -n 's/.*name="csrfmiddlewaretoken" value="\([^"]*\)".*/\1/p' | head -1)
code=$(https -b "$workdir/c2" -o /dev/null -w '%{http_code}' -H 'Referer: https://localhost/login/' \
  --data-urlencode "csrfmiddlewaretoken=$t" --data-urlencode "username=smoke-admin" \
  --data-urlencode "password=$admin_password" https://localhost/login/)
[ "$code" = "429" ] || fail "throttle returned $code"

echo "== attendance e-mail goes through the outbox, the worker and the mail server"
compose exec -T web python manage.py shell -v 0 -c "
from django.contrib.auth.models import User
from django.utils import timezone
from app1.models import InstitutionSettings, StaffNotificationPreference, Student
from app1.services.attendance import record_event
c = InstitutionSettings.load(); c.name = 'Smoke Test Institute'; c.contact_email = 'office@localhost'
c.notifications_enabled = True; c.save()
u = User.objects.get(username='smoke-admin')
StaffNotificationPreference.objects.create(user=u, receive_check_in=True, verified_email=u.email, verified_at=timezone.now())
s = Student.objects.create(name='Synthetic Smoke', email='smoke@localhost', registration_number='SMOKE-1', branch='CSE', year='1st Year')
record_event(s, 'check_in', actor=u, source='manual', reason='smoke test')
" >/dev/null
for i in $(seq 1 30); do
  count=$(curl -s http://127.0.0.1:8025/api/v1/messages | python3 -c 'import json,sys; print(json.load(sys.stdin)["total"])')
  [ "$count" -ge 1 ] && break
  sleep 2
done
[ "$count" -ge 1 ] || fail "no e-mail reached the mail catcher"
status=$(compose exec -T web python manage.py shell -v 0 -c "from app1.models import OutboundEmail; print(OutboundEmail.objects.get().status)")
[ "$status" = "sent" ] || fail "outbox status $status"
sleep 5
[ "$(docker inspect --format '{{.State.Health.Status}}' "$(compose ps -q worker)")" = "healthy" ] || \
  compose exec -T worker python manage.py run_notification_worker --healthcheck >/dev/null || fail "worker unhealthy"

echo "== backup and restore"
mkdir -p "$workdir/backup"
sh deploy/backup.sh "$workdir/backup" >/dev/null
compose exec -T web python manage.py shell -v 0 -c "
from app1.models import Student
Student.objects.create(name='After Backup', email='after@localhost', registration_number='AFTER-1', branch='CSE', year='1st Year')
" >/dev/null
dump=$(ls "$workdir"/backup/db-*.dump)
tarball=$(ls "$workdir"/backup/private-*.tar.gz)
sh deploy/restore.sh "$dump" "$tarball" >/dev/null
for i in $(seq 1 60); do
  [ "$(https -o /dev/null -w '%{http_code}' https://localhost/healthz)" = "200" ] && break
  sleep 2
done
result=$(compose exec -T web python manage.py shell -v 0 -c "
from app1.models import Student
print(Student.objects.filter(registration_number='SMOKE-1').exists(), Student.objects.filter(registration_number='AFTER-1').exists())")
[ "$result" = "True False" ] || fail "restore check: $result"

echo "SMOKE TEST PASSED"
