"""Container health check for the web service: GET /healthz on the local gunicorn.

Sends the first configured allowed host (Django rejects unknown hosts) and marks the
request as already HTTPS, as the reverse proxy would."""
import os
import sys
import urllib.request

host = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0].strip().lstrip(".") or "localhost"
request = urllib.request.Request(
    "http://127.0.0.1:8000/healthz", headers={"Host": host, "X-Forwarded-Proto": "https"}
)
try:
    with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - fixed local URL
        sys.exit(0 if response.status == 200 else 1)
except Exception:  # noqa: BLE001
    sys.exit(1)
