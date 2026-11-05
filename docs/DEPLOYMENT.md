# Deployment

This guide deploys the application with Docker Compose on one Linux server. The stack has:

| Service | What it runs |
|---|---|
| `db` | PostgreSQL 16 |
| `web` | gunicorn with Django production settings |
| `worker` | the notification worker |
| `scheduler` | checks every five minutes whether the daily summary is due |
| `proxy` | Caddy, for HTTPS and static files |
| `mailpit` | optional mail catcher for trials (`--profile mailtest`) |

The whole path is exercised by `deploy/smoke-test.sh`, which CI runs on every push.

## Before you start

- A server with Docker Engine and the Compose plugin. 2 CPU cores and 2 GB of RAM are enough for
  a single campus.
- A DNS name pointing at the server, with ports 80 and 443 open. Caddy obtains and renews the
  certificate automatically.
- The owner-only steps in [INCIDENT_CLEANUP_RUNBOOK.md](INCIDENT_CLEANUP_RUNBOOK.md). Do not
  reuse the old published password or secret key anywhere.

Browsers only allow the kiosk camera over HTTPS (or `http://localhost` during development).

## 1. Configure

```bash
cp deploy/env.production.example .env
chmod 600 .env
```

Replace every placeholder. In particular:

- `SITE_ADDRESS`, `SITE_URL`, `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` all name
  the same host.
- `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` are long random values, and the password is
  repeated in `DATABASE_URL`. Compose hands `POSTGRES_PASSWORD` to the database container as a
  Docker secret (`/run/secrets/db_password`), so it does not appear in `docker inspect`.
- The SMTP settings come from your mail provider (see [NOTIFICATIONS.md](NOTIFICATIONS.md)).
  Leave `EMAIL_HOST` empty to keep e-mail off.

Production settings refuse to start without a strong secret key and an explicit host list.

**Reverse-proxy assumptions.** TLS ends at Caddy. Caddy sets `X-Forwarded-Proto` itself, which is
why `.env` sets `DJANGO_TRUST_X_FORWARDED_PROTO=true`. If you put another proxy in front, it must
overwrite that header on every request; otherwise leave the setting off. Django then:

- redirects plain HTTP to HTTPS (except the health endpoints);
- sends HSTS for one year;
- marks session and CSRF cookies `Secure`;
- rejects unknown host names.

## 2. Build, migrate, provision models, create the first admin

```bash
docker compose build
docker compose run --rm web migrate          # also creates the cache table and runs a preflight
docker compose run --rm web setup-models     # downloads and checksum-verifies the face models
docker compose run --rm web manage createsuperuser
```

If the build runs behind a proxy that inspects TLS, give pip the proxy's CA as a build secret. It
is not stored in the image:

```bash
docker build --secret id=pip_ca,src=/path/to/ca.pem -t attendance-app:local .
```

If the server cannot download the models, copy the two verified files into a directory and run
`docker compose run --rm -v /path/to/models:/models:ro web setup-models --from-dir /models`.

The image contains no `.env`, database, photos, model files or accounts. It runs as an
unprivileged user (uid 10001).

## 3. Start

```bash
docker compose up -d
docker compose ps
```

Open `https://<your host>/`, log in, and complete *Settings* (institution name and contact).

### Where data lives

| Volume | Contents | Mounted into |
|---|---|---|
| `pgdata` | the database | `db` |
| `private` | enrolment photos (`/data/private_media`) and face models (`/data/models`) | `web`, `worker`, `scheduler` |
| `static` | CSS, JavaScript, fonts | `web` (writes), `proxy` (read-only) |
| `caddy_data` | TLS certificates | `proxy` |

The proxy has no access to the `private` volume, so no URL can reach a photo file. Photos are only
served by the application, after a permission check.

## Health and monitoring

- `GET /healthz` returns `ok` when the web process answers. `GET /readyz` checks the database and
  that all migrations are applied. Neither reveals configuration.
- The `web` container's health check calls `/healthz`. The `worker` health check runs
  `manage.py run_notification_worker --healthcheck`, which fails if no worker has reported in
  recently.
- Containers restart automatically (`restart: unless-stopped`). Logs go to Docker's JSON log
  driver, capped at 10 MB × 5 files per service. View them with `docker compose logs -f web worker`.
- The Today page lists operational problems: models missing, failed e-mails, a silent worker, or
  e-mail switched on without a mail server.

## Workers and scaling

- `GUNICORN_WORKERS` (default 3) sets the number of web processes. Login throttling and the kiosk
  and test-e-mail rate limits use the database cache, so they apply across processes and
  containers. The smoke test checks this with three workers.
- The cached set of face templates is per process, but it rebuilds as soon as a cheap database
  check shows a change, so changes made in one process reach all of them.
- `KIOSK_MAX_CONCURRENT_ANALYSES` limits simultaneous face analyses per process. Extra frames get a
  "busy" response instead of queueing.
- You can run more notification workers (`docker compose up -d --scale worker=2`). On PostgreSQL
  they never claim the same message.

## Upgrades

```bash
deploy/backup.sh /secure/backups              # always back up first
git pull
docker compose build
docker compose run --rm web migrate
docker compose up -d
```

`migrate` stops with an explanation, and changes nothing, if existing data conflicts with new
constraints. Resolve the listed rows as described in [UPGRADING.md](UPGRADING.md), then run it
again.

## Rollback

1. `docker compose stop web worker scheduler`
2. Check out the previous release and `docker compose build`.
3. If the newer release applied migrations, restore the database backup taken before the upgrade
   (`deploy/restore.sh`) instead of reverse-migrating. Some migrations, such as the one removing
   the camera configuration table, cannot restore data.
4. `docker compose up -d`, then check the Today page.

## Local trial with HTTPS and a mail catcher

```bash
cp deploy/env.production.example .env
# set SITE_ADDRESS=localhost, SITE_URL=https://localhost, DJANGO_ALLOWED_HOSTS=localhost,
# DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost, EMAIL_HOST=mailpit, EMAIL_PORT=1025, EMAIL_USE_TLS=false
docker compose --profile mailtest up -d
```

Caddy uses its own local certificate authority for `localhost`, so the browser shows a warning.
Mailpit's inbox is at <http://127.0.0.1:8025>.
