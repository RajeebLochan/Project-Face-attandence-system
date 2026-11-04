#!/bin/sh
# Container entrypoint. Usage: web | worker | scheduler | migrate | setup-models | manage <args>
set -eu

wait_for_database() {
  tries=0
  until python -c "import django; django.setup(); from django.db import connection; connection.ensure_connection()" 2>/dev/null
  do
    tries=$((tries + 1))
    if [ "$tries" -ge 30 ]; then
      echo "Database is not reachable after 60 seconds." >&2
      exit 1
    fi
    sleep 2
  done
}

command="${1:-web}"
[ "$#" -gt 0 ] && shift

case "$command" in
  web)
    wait_for_database
    # Copy collected static files into the shared volume the proxy serves.
    if [ -n "${STATIC_SHARE_DIR:-}" ]; then
      cp -r /app/staticfiles/. "$STATIC_SHARE_DIR"/
    fi
    exec gunicorn Project101.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout "${GUNICORN_TIMEOUT:-30}" \
      --max-requests 2000 --max-requests-jitter 200 \
      --access-logfile - --error-logfile -
    ;;
  worker)
    wait_for_database
    exec python manage.py run_notification_worker "$@"
    ;;
  scheduler)
    wait_for_database
    exec python manage.py send_daily_digest --if-due --loop "${DIGEST_CHECK_SECONDS:-300}"
    ;;
  migrate)
    wait_for_database
    python manage.py migrate --noinput
    python manage.py createcachetable
    python manage.py attendance_preflight
    ;;
  setup-models)
    exec python manage.py setup_face_models "$@"
    ;;
  manage)
    exec python manage.py "$@"
    ;;
  *)
    exec "$command" "$@"
    ;;
esac
