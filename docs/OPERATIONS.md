# Operations: backups, restores and retention

## What to back up

| Data | Where | How |
|---|---|---|
| Database (students, attendance, audit history, consent, e-mail queue) | PostgreSQL `pgdata` volume | `deploy/backup.sh` (`pg_dump --format=custom`) |
| Enrolment photos | `private` volume, `/data/private_media` | `deploy/backup.sh` (tar) |
| Face models | `private` volume, `/data/models` | Not needed: run `setup-models` again |
| Static files | `static` volume | Not needed: rebuilt from the image |
| `.env` | next to `docker-compose.yml` | Store separately, in a password manager or secret store |

```bash
deploy/backup.sh /secure/backups
```

This writes `db-STAMP.dump`, `private-STAMP.tar.gz` and a `SHA256SUMS-STAMP` file, readable only
by the user running it.

Backups contain personal and biometric data:

- Encrypt them at rest and in transit.
- Keep them off the application server.
- Limit who can read them.
- Delete them when your retention period ends.

## Restore

```bash
deploy/restore.sh /secure/backups/db-STAMP.dump /secure/backups/private-STAMP.tar.gz
```

The script:

1. stops the application containers;
2. restores the database in one transaction;
3. replaces the photo store;
4. runs migrations;
5. starts everything again.

Always restore both files from the same backup.

**After a restore**, consent withdrawals, biometric removals and student deletions made after the
backup are back. Re-apply them using the audit history (*Audit history*, filter by "Biometric
data removed" or "Student deleted", from the backup date onwards). Then run:

```bash
docker compose run --rm web manage attendance_preflight
docker compose run --rm web manage rebuild_face_embeddings
```

**Restore drills.** `deploy/smoke-test.sh` performs a backup, a change and a restore on a
throwaway stack, and checks that the change is gone. Repeat a drill with your own procedure at
least once a term.

## Retention and deletion

- **Consent withdrawn or no longer needed.** *Remove biometric data* on the student page deletes
  the photo file (after the database change commits) and all face templates. Attendance stays.
- **Student left.** *Mark as left* from a date keeps their history and stops them appearing on the
  register.
- **Full deletion** is possible only for students with no attendance records.
- **E-mail queue.** Message bodies stay in the database for reference. Decide a retention period,
  for example 90 days, and delete old rows from a Django shell or a scheduled job of your own;
  there is no automatic purge.
- **Retention period.** Set it in your privacy notice (`templates/privacy_notice.html`) and follow
  it, including for backups.

## Routine checks

- **Daily:** the Today page lists operational issues. Resolve missing check-outs from earlier days.
- **Weekly:** look at failed e-mails on the Notifications page; check backups ran.
- **Each term:**
  - review staff accounts and batch assignments;
  - run `pip-audit -r requirements.txt` (or check CI);
  - do a restore drill.
