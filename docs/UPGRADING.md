# Upgrading an Existing Installation

These steps apply to an installation running the audited version (migrations up to
`0013_student_year`). The upgrade **never merges or deletes conflicting records
automatically**. If existing data violates the new integrity rules, migration `0015` stops
with an explanation and changes nothing.

## 1. Before you start

1. Complete the credential and secret rotation in
   [`INCIDENT_CLEANUP_RUNBOOK.md`](INCIDENT_CLEANUP_RUNBOOK.md).
2. **Back up** the database and the old `media/` directory to an access-controlled location.
   Both contain personal data.
3. Stop the old server. The old "Mark Attendance" view opened server cameras and must not run
   again.

## 2. Install the new code and dependencies

```bash
git pull
python3.12 -m venv .venv            # Python 3.12 or 3.13
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                # then edit: never commit .env
```

The default database is still `db.sqlite3` in the project directory. To keep a database
elsewhere, set `DATABASE_URL` (for example `DATABASE_URL=sqlite:////srv/attendance/db.sqlite3`).

## 3. Check for conflicts before migrating

```bash
python manage.py attendance_preflight
```

The command prints **primary keys only** (never names or registration numbers):

- `BLOCKING:` items must be fixed before `migrate` succeeds.
- `WARNING:` items do not block the upgrade, but need attention.

### Resolving blocking conflicts

Decide each case with someone who knows the real records. Use the Django shell
(`python manage.py shell`); the IDs come from the preflight output.

**Two students share a registration number.** Find out which record is correct, then either
correct the other student's number or delete a true duplicate that has no attendance:

```python
from app1.models import Student
Student.objects.filter(pk=12).update(registration_number="CORRECTED-NO")
```

**Several attendance rows for one student on one date** (caused by the old midnight bug).
Look at the rows, keep one, and copy the earliest check-in and latest check-out into it:

```python
from app1.models import Attendance
rows = Attendance.objects.filter(pk__in=[40, 41, 42]).order_by("pk")
for r in rows: print(r.pk, r.check_in_time, r.check_out_time)
# decide which values are right, update one row, then delete the others explicitly
```

**Check-out without check-in.** Set the correct check-in time on the row, or clear the
check-out.

Run `attendance_preflight` again until no `BLOCKING:` lines remain.

## 4. Migrate

```bash
python manage.py migrate
```

Migration `0014` adds consent, audit and face-template tables, widens `year`, and **drops the
obsolete camera-configuration table**. Server-side camera sources are no longer supported.
Migration `0015` adds the unique and check constraints after verifying the data again.

Existing students keep their `authorized` flag, but their consent is recorded as
**"Not recorded"**. Nobody is recognised until consent is recorded and a face template exists
for the new model.

## 5. Move photos out of the public media directory

```bash
python manage.py relocate_legacy_photos --source media
```

This re-encodes each photo (dropping embedded metadata), stores it in `PRIVATE_MEDIA_ROOT`
under a random name, and updates the student record. Add `--delete-source` to remove each
original after a successful copy. After verifying the result, **delete the old `media/`
directory** and remove any web-server alias that pointed at it.

## 6. Record consent and create face templates

The old system never recorded consent. For each student who should use face recognition:

1. Give them (or their parent/guardian) the completed privacy notice and get written consent.
2. Open the student, choose **Replace photo**, tick the consent box, and capture a new photo.
   This records consent and creates the face template.
3. An attendance admin reviews the photo and authorises the student.

For students who do not consent, use **Remove biometric data**: this deletes their photo and
template and keeps their attendance history. Record their attendance with **Manual
attendance**.

## 7. Install models and finish

```bash
python manage.py setup_face_models
python manage.py rebuild_face_embeddings     # only needed after model or preprocessing changes
python manage.py attendance_preflight        # should report no conflicts
```

Then assign staff to the **Attendance admin** or **Instructor** group in `/admin/`.

## Upgrading to the release with batches and notifications (migration 0016)

- Run `migrate` as usual; there are no new blocking constraints. Existing students get an
  `enrolled_on` date equal to the earlier of their creation date and their first attendance, so
  past days are not reported as "not enrolled".
- Production settings now use the database cache. Run `python manage.py createcachetable` once
  (the container `migrate` command does this).
- Batches are optional. Until you create them and assign students:
  - instructors see no students;
  - nothing is reported late or absent.
  Assign instructors to batches on the *Staff* page.
- E-mail notifications stay off until you configure SMTP, complete *Settings* and verify
  recipients (see [NOTIFICATIONS.md](NOTIFICATIONS.md)).
