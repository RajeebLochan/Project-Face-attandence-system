# Face Recognition Attendance System

A Django web application that records student attendance with **staff-confirmed face
recognition** from a browser webcam, plus manual attendance and audited corrections. It runs
on a headless server: the browser owns the camera, and the server only analyses frames it
receives.

> **Security notice.** Earlier public versions of this repository exposed an administrator
> password, the Django secret key, and students' photos and personal data. Removing them from
> the latest commit does not remove them from Git history or from existing copies. The owner
> actions are in [`docs/INCIDENT_CLEANUP_RUNBOOK.md`](docs/INCIDENT_CLEANUP_RUNBOOK.md). That
> exposure remains open until the runbook is completed.

**Status:** not production-ready. It is suitable for a *supervised pilot* only after:

- the incident runbook is completed,
- the institution completes the privacy notice and retention policy, and
- recognition is evaluated on consented local data.

See [Limitations](#limitations).

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Local setup](#local-setup)
- [Roles and daily use](#roles-and-daily-use)
- [Face recognition: what it does and does not do](#face-recognition-what-it-does-and-does-not-do)
- [Privacy, consent and retention](#privacy-consent-and-retention)
- [Configuration](#configuration)
- [Testing and checks](#testing-and-checks)
- [Deployment](#deployment)
- [Backup and restore](#backup-and-restore)
- [Performance](#performance)
- [Upgrading from the previous version](#upgrading-from-the-previous-version)
- [Limitations](#limitations)

## How it works

1. **Enrolment (Attendance admin).** Staff record the student's consent against a versioned
   privacy notice, enter their details and capture one photo. The server checks the image and
   requires **exactly one face**. It stores the photo privately under a random file name and
   saves a face template linked to the student's ID.
2. **Authorisation (Attendance admin).** An admin reviews the protected photo and authorises
   recognition. Without consent, a photo and a current template, authorisation is refused.
3. **Kiosk (Instructor or admin).**
   - The browser kiosk shows a camera preview and sends one downscaled frame at a time to the
     server.
   - The server answers *candidate*, *unknown*, *ambiguous*, *unsuitable frame* (no face,
     several faces, face too small) or *model unavailable*.
   - A candidate is never recorded automatically: the staff member compares the person with
     the enrolment photo and confirms or rejects.
   - Check-in and check-out are explicit modes. Repeating an event that is already recorded
     changes nothing.
4. **Manual attendance and corrections (Instructor or admin).** These are available for
   everyone, require a reason, and are written to the audit log with before/after values.

More detail: [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md).

## Requirements

- **Python 3.12 or 3.13** (both tested; numpy 2.5 requires ≥ 3.12). Linux, macOS or Windows.
- About 40 MB for the face models, downloaded once by a management command.
- A modern browser with a webcam on the kiosk device.
  - Browsers only allow camera access on **HTTPS** or on `http://localhost`.
- Optional: PostgreSQL 14+ (recommended when several worker processes write at once).

No GPU, PyTorch, display, audio device or server-side camera is needed.

## Local setup

```bash
git clone https://github.com/RajeebLochan/Project-Face-attandence-system.git
cd Project-Face-attandence-system
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # optional for development; never commit .env
python manage.py migrate             # creates db.sqlite3 and the role groups
python manage.py createsuperuser     # choose your own credentials
python manage.py setup_face_models   # downloads and SHA-256-verifies the models into var/models/
python manage.py runserver
```

Open <http://localhost:8000/>, log in, and go to `/admin/` → *Users* to add staff to the
**Attendance admin** or **Instructor** group.

Without internet access on the server, download the two model files elsewhere from the
URLs in `app1/recognition/model_files.py` and run
`python manage.py setup_face_models --from-dir /path/to/files`. Checksums are still
verified. Without models, enrolment and the kiosk show a clear message, and manual
attendance still works.

## Roles and daily use

| Role | Can |
|---|---|
| **Attendance admin** (group) | Enrol students, edit details, replace photos, authorise or revoke, remove biometric data, delete students without attendance history, plus everything an Instructor can do, plus the audit log |
| **Instructor** (group) | Use the kiosk, view students and enrolment photos, view and export attendance, record manual attendance, correct records |
| Superuser | Everything, including user and group management in `/admin/` |
| Any other logged-in user | Sees a "403 Access denied" page |

The groups and their permissions are defined in `app1/roles.py` and re-synchronised on every
`migrate` (or with `python manage.py sync_roles`). The Django admin is read-only for app data,
so that all changes go through validated, audited views.

## Face recognition: what it does and does not do

- **Pipeline:**
  - Detection: OpenCV's **YuNet** detector finds the face and five landmarks.
  - Embedding: `FaceRecognizerSF.alignCrop` aligns the face, and **SFace** produces a
    128-dimensional embedding, which is L2-normalised.
  - Model source: both models come from a pinned commit of the OpenCV Model Zoo and are
    checked against SHA-256 hashes (`app1/recognition/model_files.py`).
- **Matching:**
  - The distance is the Euclidean distance between normalised embeddings, in the range 0–2.
  - A match needs a distance below `FACE_MATCH_THRESHOLD` (default **1.128**). The best
    student must also beat the next-best different student by at least `FACE_MATCH_MARGIN`
    (default 0.10); otherwise the result is *ambiguous* and nobody is proposed.
  - **A distance is not a confidence or a probability.**
- **Not calibrated:** 1.128 is the value OpenCV publishes for SFace. It has **not** been
  validated for this deployment, its cameras or its population. Before relying on it, measure
  false-accept and false-reject rates on a consented, labelled local dataset and choose the
  threshold from a target false-accept rate.
- **No liveness detection:** a printed photo or a screen may be accepted. This is why every
  match requires a staff member to confirm, and why no unsupervised mode exists. Checking that
  several frames agree would not be liveness detection, and none is claimed.
- **One face per frame:** the kiosk asks for one person at a time.
- **Templates are versioned:** after changing models or preprocessing (`MODEL_VERSION`), run
  `python manage.py rebuild_face_embeddings`.
- **No accuracy figures are claimed** anywhere in this project. The automated tests use a fake
  engine and synthetic images, which prove the software logic but say nothing about
  recognition accuracy.

## Privacy, consent and retention

- **Consent first:** the enrolment form requires staff to confirm that the student (or their
  parent or guardian, if under 18) received privacy notice version `PRIVACY_NOTICE_VERSION`
  and consented. The status, time, notice version and staff member are stored. The institution
  must complete `templates/privacy_notice.html` (controller, contact, retention) before use.
- **Withdrawal:** *Remove biometric data* deletes the photo file (after the transaction
  commits) and all face templates, revokes recognition and records consent as withdrawn.
  Attendance history is kept. Students without consent use manual attendance.
- **Deletion:** students without attendance history can be deleted completely. Attendance
  records are protected from silent cascade deletion.
- **Storage:**
  - Photos are re-encoded as JPEG (embedded metadata removed), stored with `0600`
    permissions under random names in `PRIVATE_MEDIA_ROOT`, and served only by a
    permission-checked view with `Cache-Control: private, no-store`.
  - Kiosk frames are never stored.
  - Logs contain IDs only, never names, images or embeddings.
- **Retention:** the institution must set a retention period, for example deleting biometric
  data when a student leaves, and must review the audit log (`/audit/`) periodically.

## Configuration

All settings come from environment variables (or a local `.env` file). See
[`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev placeholder | **Required in production**: ≥ 50 random characters |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` (dev) | **Required in production**; `*` refused |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | – | e.g. `https://attendance.example.edu` |
| `DJANGO_TRUST_X_FORWARDED_PROTO` | `false` | Set to `true` only behind a proxy that overwrites this header |
| `DATABASE_URL` | `sqlite:///db.sqlite3` | or `postgres://user:pass@host:5432/db` |
| `PRIVATE_MEDIA_ROOT` | `var/private_media` | Enrolment photos; **never serve this directory** |
| `FACE_MODEL_DIR` | `var/models` | ONNX models |
| `FACE_MATCH_THRESHOLD` | `1.128` | L2 distance limit, between 0 and 2 (not a confidence) |
| `FACE_MATCH_MARGIN` | `0.10` | Required gap to the next-best student |
| `FACE_MIN_SIZE_PX` | `64` | Smallest usable face (in the downscaled analysis image) |
| `KIOSK_MIN_FRAME_INTERVAL_SECONDS` | `0.5` | Per-user frame rate limit |
| `KIOSK_MAX_CONCURRENT_ANALYSES` | `1` | Analyses at once per process; extra frames get "busy" |
| `KIOSK_FRAME_MAX_BYTES` / `ENROLMENT_PHOTO_MAX_BYTES` | 1.5 MB / 5 MB | Upload limits |
| `PRIVACY_NOTICE_VERSION` | `1.0` | Recorded with each consent |
| `LOGIN_THROTTLE_MAX_FAILURES` | `5` per 15 min | Per username (per address: 50) |

Invalid numeric values (non-numbers, `nan`, `inf`, out of range) stop start-up with a clear
error.

## Testing and checks

```bash
pip install -r requirements-dev.txt
python manage.py test                                   # full suite (SQLite file DB, fake engine)
python manage.py check
python manage.py makemigrations --check --dry-run
ruff check .
pip-audit -r requirements.txt
```

Optional suites:

```bash
# PostgreSQL (incl. real multi-thread concurrency tests)
pip install -r requirements-postgres.txt
DATABASE_URL=postgres://postgres:postgres@localhost:5432/attendance python manage.py test

# Real models (after setup_face_models); verifies loading, checksums and pipeline, not accuracy
FACE_TEST_MODEL_DIR=var/models python manage.py test --tag model

# Browser kiosk tests with Chromium's fake camera
python -m playwright install chromium
python manage.py test --tag browser

# Production deployment checks with representative settings
DJANGO_SETTINGS_MODULE=Project101.settings.prod DJANGO_SECRET_KEY=<50+ random chars> \
  DJANGO_ALLOWED_HOSTS=attendance.example.edu DJANGO_SECURE_HSTS_PRELOAD=true \
  python manage.py check --deploy --fail-level WARNING
```

Tests that need models or a browser skip themselves when those are not installed. CI
(`.github/workflows/ci.yml`) runs lint, the suite on SQLite (Python 3.12 and 3.13) and
PostgreSQL 16, real-model checks, browser tests, the deployment check and `pip-audit`.

Physical checks that automated tests cannot cover:

- real webcams on the target kiosk devices and browsers,
- lighting and camera placement,
- recognition error rates on consented local data.

## Deployment

Production settings (`Project101.settings.prod`, the default for `wsgi.py`/`asgi.py`) force
`DEBUG` off. They also enable HTTPS redirect, HSTS, secure cookies, CSP and
Permissions-Policy headers.

```bash
export DJANGO_SETTINGS_MODULE=Project101.settings.prod   # plus the variables above
pip install -r requirements.txt                          # or requirements-postgres.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py setup_face_models
python manage.py createsuperuser
gunicorn Project101.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 30
```

**Reverse-proxy assumptions** (example for nginx):

```nginx
server {
    listen 443 ssl;
    server_name attendance.example.edu;
    # ssl_certificate ...; ssl_certificate_key ...;
    client_max_body_size 6m;
    location /static/ { alias /srv/attendance/staticfiles/; }
    # Do NOT add any location for PRIVATE_MEDIA_ROOT or /media/.
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;   # overwrite, then DJANGO_TRUST_X_FORWARDED_PROTO=true
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

**Deployment notes:**

- Each worker process loads the models once (about 40 MB of RAM) on its first recognition
  request.
- `KIOSK_MAX_CONCURRENT_ANALYSES` applies per process.
- The gallery cache is per process and refreshes when the database changes.
- Login throttling uses `REMOTE_ADDR`. Behind a proxy that is the proxy's address, so the
  per-address limit is deliberately high; the per-username limit still applies.
- Use the file cache (default in production, `DJANGO_CACHE_DIR`) on a single host. Use a
  shared cache for several hosts.
- Errors are logged to stderr without request bodies. Set `DJANGO_ADMIN_EMAILS` and an e-mail
  backend, or forward the logs, to be alerted.

## Backup and restore

- **Back up:**
  - the database (`db.sqlite3`, or `pg_dump` for PostgreSQL), and
  - `PRIVATE_MEDIA_ROOT`.
  Both contain personal data: encrypt backups, restrict access, and keep them only as long as
  the retention policy allows.
- **Not needed:** `var/models` (re-download with `setup_face_models`) and `staticfiles`
  (re-run `collectstatic`).
- **For SQLite,** back up with `sqlite3 db.sqlite3 ".backup backup.sqlite3"` (safe while
  running) rather than copying the file.
- **Restore** the database and the private media from the *same* point in time, then run
  `python manage.py attendance_preflight` and `python manage.py rebuild_face_embeddings`.
- **Deletions done after a backup** (consent withdrawals, removed students) reappear when you
  restore it. Re-apply them from the audit log (`biometrics_removed`, `student_deleted`
  events) after any restore.
- Test restores regularly.

## Performance

**Measured** on a 4-vCPU cloud container (CPU only, Python 3.12, synthetic frames without real
faces):

| Operation | Time |
|---|---|
| Engine load, including SHA-256 check (once per process) | 0.27 s |
| YuNet detection, 640×480 / 1280×720 / 1920×1080 frame (downscaled to 640) | 16.7 / 17.3 / 14.9 ms |
| Alignment + SFace embedding, per face | 22.3 ms |
| Gallery match vs 50 / 500 / 5,000 students | 0.03 / 0.12 / 1.3 ms |
| Gallery signature check per frame (SQLite), 50 / 500 / 5,000 students | 1.1 / 1.4 / 6.0 ms |
| Gallery rebuild after a change, 50 / 500 / 5,000 students | 4 / 8 / 71 ms |
| Full kiosk request, 640×480 JPEG, no face (decode + detect + response) | 33 ms median, 38 ms p90 |

**Estimates** (not measured end to end, because no real faces were used):

- A frame with one face should take roughly 60 ms of server time (the request above plus one
  embedding and a match).
- Per-frame cost hardly depends on how many students are enrolled. 50, 500 and 5,000 students
  are all practical on one CPU core per kiosk.
- The browser sends at most one frame at a time, so throughput is limited by the network and
  `KIOSK_MIN_FRAME_INTERVAL_SECONDS`.

The old design re-encoded every enrolled student on every frame (≈ 8 s/frame at 50 students).

Re-run the benchmark: `FACE_MODEL_DIR=var/models python scripts/benchmark_recognition.py`.

## Upgrading from the previous version

Follow [`docs/UPGRADING.md`](docs/UPGRADING.md). In short:

1. Back up.
2. Run `attendance_preflight` and resolve the blocking conflicts it lists. Nothing is merged
   automatically.
3. Run `migrate`.
4. Run `relocate_legacy_photos --source media`.
5. Re-record consent with a new photo for each student who should use recognition.
6. Run `setup_face_models`.

The audit findings and their fixes are mapped in
[`docs/REMEDIATION_CHECKLIST.md`](docs/REMEDIATION_CHECKLIST.md).

## Limitations

- **Recognition:**
  - accuracy, false-accept and false-reject rates are unmeasured, and the threshold is
    uncalibrated;
  - there is no liveness or anti-spoofing protection;
  - there is no evaluation across lighting, cameras or demographics.
- **Untested hardware and browsers:** real webcams, kiosk devices, and mobile Safari/Firefox
  camera behaviour have not been tested; only Chromium's fake camera was used.
- **No timetable data:** there are no courses or sessions, so no absence, lateness or
  attendance percentages.
- **Single-host defaults:** SQLite and the file cache. Use PostgreSQL and a shared cache to
  scale out.
- **History exposure still open:** the exposure of credentials and personal data in Git
  history remains until the owner completes the incident runbook.

## Project layout

```
Project101/settings/    base.py, dev.py, prod.py, test.py
app1/models.py          Student, FaceEmbedding, Attendance, AuditEvent
app1/recognition/       engine (lazy, OpenCV), model_files (pinned + checksums), gallery, service
app1/services/          images (validation), attendance (transactions), students (lifecycle)
app1/views/             auth, dashboard, students, attendance, kiosk, errors
app1/management/        setup_face_models, rebuild_face_embeddings, attendance_preflight,
                        relocate_legacy_photos, sync_roles
app1/tests/             unit, integration, migration, concurrency, browser and real-model tests
templates/, static/     base template, one stylesheet, kiosk.js, photo_capture.js
docs/                   incident runbook, upgrade guide, remediation checklist, overview
```
