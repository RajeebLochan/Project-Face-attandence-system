# Attendance

A Django web application for recording student attendance at a school, college or training centre.
Staff check students in and out at a browser-based kiosk that suggests a match from the camera
image, or record attendance by hand. Every match is confirmed by a person before anything is saved.

It is built for two kinds of users:

- **Attendance admins**, who enrol students, review photos, manage batches, schedules, leave and
  staff accounts, and look after e-mail notifications.
- **Instructors**, who run the kiosk, record manual attendance and corrections, and see reports for
  the batches they are assigned to.

> **Before you deploy this**
>
> - Earlier versions of this repository published an administrator password, the Django secret
>   key and students' photos and personal data. They are still in Git history and in any copies
>   made at the time. The steps that only the repository owner can take are in
>   [docs/INCIDENT_CLEANUP_RUNBOOK.md](docs/INCIDENT_CLEANUP_RUNBOOK.md); that work is not done yet.
> - Face matching has not been evaluated on real people. Its error rates are unknown, the
>   threshold is the model author's published default, and there is no protection against a
>   printed photo or a screen. Treat recognition as a convenience for staff, never as proof.

![Today page with the day's register](docs/screenshots/dashboard.png)

## What it does

- **Today page.** Who has checked in, who is late or not yet recorded, a timeline of check-ins
  against the expected start, photos waiting for review, missing check-outs from earlier days, and
  operational problems such as failed e-mails or a stopped worker.
- **Kiosk.**
  - The browser shows a live camera preview and sends one small frame at a time.
  - The server proposes a student by ID, and the staff member confirms or rejects.
  - Check-in and check-out are separate, explicit modes.
- **Manual attendance.**
  - Search by name or registration number, see today's status, then check in or out with a reason.
  - Past entries and corrections are separate, audited actions that need a reason.
  - A missing check-out is never filled in automatically.
- **Students.**
  - Search, filters and pagination; CSV import with a preview and duplicate detection.
  - Consented photo enrolment, active and left status, and batch membership with dates.
  - Contact details for the student and a parent or guardian, with per-recipient e-mail preferences.
- **Batches and schedules.**
  - Working days, expected start time, grace period and holidays.
  - Changes take effect from a date, so earlier reports are not rewritten.
- **Leave** that turns an absence into "on leave".
- **Reports.**
  - Status counts per student over a date range, filtered by batch.
  - CSV export and a printable daily register.
- **E-mail notifications** for check-ins, check-outs and (optionally) corrections, sent to verified
  staff, students or guardians, plus an optional daily summary. Details are in the
  [Notifications](#notifications) section.
- **Audit history** of every attendance change, enrolment, consent change, setting change and
  staff change, searchable by student, staff member, action and date.

Attendance does not depend on face recognition. Students who decline it, or whose consent is
withdrawn, are recorded manually like everyone else.

| | |
|---|---|
| ![Kiosk confirming a match](docs/screenshots/kiosk.png) | ![Manual attendance](docs/screenshots/manual-attendance.png) |
| ![Student record](docs/screenshots/student-detail.png) | ![Attendance report](docs/screenshots/reports.png) |
| ![Notification queue](docs/screenshots/notifications.png) | ![Settings](docs/screenshots/settings.png) |

The screenshots use invented people and drawn silhouettes, not faces. More are in
[docs/screenshots](docs/screenshots); the `before-*` files show the previous interface.

## Requirements

- **Python:** 3.12 or 3.13. Both are tested; numpy 2.5 needs at least 3.12.
- **Database:** PostgreSQL 14 or newer in production. SQLite is fine for development and a
  single-host trial.
- **Face models:** about 40 MB, downloaded once with `setup_face_models`. Everything runs on the
  CPU; there is no GPU, PyTorch, display or server camera requirement.
- **Kiosk:** a browser with a webcam. Browsers only allow camera access over HTTPS or on
  `http://localhost`.
- **Production stack:** Docker with the Compose plugin, if you use the included deployment.

## Quick start (development)

```bash
git clone https://github.com/RajeebLochan/Project-Face-attandence-system.git
cd Project-Face-attandence-system
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py setup_face_models
python manage.py runserver
```

Open <http://localhost:8000/> and log in. In development:

- E-mails are written as files to `var/sent-mail/` instead of being sent.
- Run `python manage.py run_notification_worker` in a second terminal to process the queue.

**Model setup without internet on the server.** `setup_face_models` downloads the YuNet detector
and SFace recogniser from a pinned commit of the OpenCV Model Zoo and checks their SHA-256 hashes.
Without internet access, download the two files listed in `app1/recognition/model_files.py`
elsewhere and run:

```bash
python manage.py setup_face_models --from-dir /path/to/files
```

Without the models, the kiosk and photo enrolment show a clear error; everything else works.

## Daily use

1. **First run.**
   - Open *Settings*: enter the institution name and a contact address for attendance questions.
   - Create batches with a schedule.
   - Add staff on the *Staff* page. Attendance admins see everything; instructors only see the
     batches you assign them, including on direct links and exports.
2. **Students.**
   - Enrol one at a time (consent, details, photo) or import a CSV.
   - A photo is used for recognition only after an admin has reviewed it on the student's page.
3. **Each day.**
   - Open the kiosk, choose *Check in* or *Check out*, and start the camera.
   - When the page proposes a student, compare the person with the enrolment photo, then press
     *Confirm* or *Not this person*. A repeated confirmation changes nothing.
4. **When the camera can't help**, use *Manual attendance*: search, pick the student, give a
   reason, and press *Check in now* or *Check out now*.
5. **Fixing mistakes.**
   - *Record a past entry* is for times that have already passed.
   - *Correct* (on any record) changes existing times.
   - Both need a reason, and the original values stay in the audit history.
6. **Unfinished days.** A check-in without a check-out on an earlier day is listed on the Today
   page until someone enters the real time or marks it as reviewed. The system never invents a
   check-out time.

### How statuses are decided

| Status | Meaning |
|---|---|
| Present | Checked in by the expected start plus the grace period |
| Late | Checked in after that |
| Checked in | Checked in, but no schedule applies, so lateness is not judged |
| Absent | A past working day with a schedule: on the roster, no check-in, no leave, no holiday |
| On leave / Holiday / No class | Approved leave, a holiday, or not a working day |
| Not yet recorded | Today, a future day, or any day without a schedule |
| Not enrolled | Before the enrolment date, after the student left, or not on a batch roster that day |

Absence is never inferred without a schedule.

## Notifications

E-mails go out for check-ins, check-outs and, if you switch it on, corrections and past entries.
Recipients can be:

- selected staff, who only hear about students they can see;
- the student;
- a parent or guardian.

Each address must be confirmed through a verification link first. If the same address appears
more than once, it gets one message. Each message contains only:

- the institution name;
- the student's name and registration number;
- the event, with the date and local time labelled with the time zone;
- whether it was recorded manually;
- the institution's contact for reporting mistakes.

There are never photos. Staff copies include a link to the record, which requires login.

**How delivery works:**

- The attendance change and its e-mails are written to the database in one transaction. A
  rolled-back change, a repeated (no-op) submission or an unconfirmed kiosk match never produces
  an e-mail.
- A separate process sends the queue:

  ```bash
  python manage.py run_notification_worker
  ```

  You can run several workers; on PostgreSQL they never claim the same message.
- Temporary failures are retried with backoff (1, 5, 15 and 60 minutes). Permanent rejections
  fail immediately.
- A worker that crashes mid-send has its messages requeued after five minutes.
- Queued messages are cancelled when the recipient's address, preference or access changes.

**Statuses** are *Queued*, *Processing*, *Accepted by mail server*, *Failed* and *Cancelled*.
"Accepted" means the outgoing server took the message, not that it reached the inbox. If the
network fails after the server accepted a message, the retry can deliver it twice; SMTP does not
offer exactly-once delivery.

**Configuration:**

- SMTP settings come from environment variables (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS` or
  `EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`,
  `EMAIL_TIMEOUT`). The password is never shown in the interface or written to logs.
- *Settings* has a rate-limited test e-mail button and previews of each message.
- The optional daily summary is off by default; it is sent at most once per address per day.

Sender-domain setup (SPF, DKIM, DMARC), local testing with Mailpit, scheduling and
troubleshooting are covered in [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md).

## Production deployment

The repository includes a Docker Compose stack:

- PostgreSQL;
- the web app under gunicorn;
- the notification worker;
- the summary scheduler;
- Caddy as the HTTPS reverse proxy;
- optional Mailpit for testing.

The proxy only receives static files. The volume with enrolment photos is mounted into the web
and worker containers, never into the proxy.

```bash
cp deploy/env.production.example .env        # then replace every placeholder
docker compose build
docker compose run --rm web migrate
docker compose run --rm web setup-models
docker compose run --rm web manage createsuperuser
docker compose up -d
```

Step-by-step instructions, the reverse-proxy assumptions, health checks, monitoring, upgrades and
rollback are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). `deploy/smoke-test.sh` brings the whole
stack up with synthetic data and checks the following, then removes it again:

- HTTPS and the security headers;
- that photos are not exposed;
- login and throttling across workers;
- e-mail delivery through the worker;
- backup and restore.

## Backup and restore

```bash
deploy/backup.sh /secure/backups
deploy/restore.sh /secure/backups/db-STAMP.dump /secure/backups/private-STAMP.tar.gz
```

- Backups contain personal and biometric data. Encrypt them, restrict access, and delete them when
  your retention period ends.
- Restore the database and the photo store from the same backup.
- Re-apply any consent withdrawals or deletions made after it. They are listed in the audit
  history; see [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Privacy controls

- **Photos:**
  - Stored outside any web-served directory, under random file names, readable only by the
    application.
  - Shown only to staff with the photo permission, with `Cache-Control: private, no-store`.
  - Re-encoded on upload, which drops embedded metadata.
- **Consent:**
  - Recorded with the time, the staff member and the privacy-notice version.
  - Recognition needs consent, a reviewed photo and authorisation.
  - *Remove biometric data* deletes the photo and face template and keeps attendance history.
- **Kiosk frames** are analysed in memory and discarded.
- **Logs** contain record IDs, not names, photos or face templates.
- **Privacy notice:** `templates/privacy_notice.html` is a template your institution must
  complete before collecting photos.

## Tests and contributing

```bash
pip install -r requirements-dev.txt
python manage.py test                     # SQLite, fake face engine, in-memory e-mail
ruff check .
python manage.py makemigrations --check --dry-run
pip-audit -r requirements.txt
```

Optional suites:

```bash
DATABASE_URL=postgres://USER:PASS@localhost:5432/DB python manage.py test   # PostgreSQL
FACE_TEST_MODEL_DIR=var/models python manage.py test --tag model            # real models
python -m playwright install chromium && python manage.py test --tag browser
SCREENSHOT_DIR=docs/screenshots python manage.py test --tag screenshots
sh deploy/smoke-test.sh                                                     # needs Docker
```

What the tests do and do not show:

- The normal suite uses a fake face engine with synthetic images and Chromium's simulated camera.
  It checks the software's rules, not recognition accuracy or real cameras.
- The real-model tests only check that the models load and run correctly on synthetic input.
- No test sends e-mail to a real provider.

CI (`.github/workflows/ci.yml`) runs lint, the suite on SQLite and PostgreSQL, the browser,
real-model and deployment checks, and a dependency audit.

Please keep changes small and covered by a test, run the commands above before opening a pull
request, and never commit `.env`, databases, photos or exports.

## Known limitations

- Recognition accuracy, false matches and missed matches have not been measured on real people;
  the threshold is not calibrated for your cameras or students. There is no liveness check, so
  staff must watch every match.
- Real webcams, tablets and browsers other than Chromium have not been tested.
- E-mail delivery to real inboxes has not been tested against a real provider, and SMTP can
  occasionally deliver a message twice.
- There are no courses, timetables or attendance percentages; schedules are per batch and per
  day.
- One attendance record per student per day (one check-in, one check-out).
- The historical data exposure described at the top still needs the owner's action.

## Further reading

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): production setup, HTTPS, health checks, upgrades and
  rollback.
- [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md): e-mail setup, the worker, the digest and
  troubleshooting.
- [docs/OPERATIONS.md](docs/OPERATIONS.md): backups, restore drills and data retention.
- [docs/UPGRADING.md](docs/UPGRADING.md): upgrading a database from the original version.
- [docs/REMEDIATION_CHECKLIST.md](docs/REMEDIATION_CHECKLIST.md): audit findings and how they
  were addressed.
- [docs/DESIGN.md](docs/DESIGN.md): interface direction and credits.
- [docs/INCIDENT_CLEANUP_RUNBOOK.md](docs/INCIDENT_CLEANUP_RUNBOOK.md): owner-only cleanup of the
  historical exposure.
