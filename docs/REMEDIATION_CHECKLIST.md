# Remediation Checklist

This maps each finding of the code audit of commit `f3dc00d` to the change that addresses it, the
tests that cover it, and what is still open. "Open" items need the repository owner, the
institution, or evidence that cannot be produced in code (for example accuracy measurements).

Test files are in `app1/tests/`.

## Critical and high

| ID | Finding | Change | Tests | Status / remaining |
|---|---|---|---|---|
| SEC-01 | Working superuser credentials published in README and committed DB | Credentials removed from README; `db.sqlite3` untracked and git-ignored; setup uses `createsuperuser` | — | **Open (owner):** change or deactivate the account everywhere; history rewrite. See `docs/INCIDENT_CLEANUP_RUNBOOK.md` |
| SEC-02 | Student photos and PII in history and in `Docomentation.docx` | Files removed from the tree; `.gitignore` covers DBs, media, private data, exports, `.docx`; sanitized `docs/PROJECT_OVERVIEW.md` replaces the docx | — | **Open (owner/institution):** history rewrite, GitHub cache removal, notification decision. Copies cannot be recalled |
| SEC-03 | Face images publicly downloadable from `/media/` | No media URL route in any settings; photos in `PRIVATE_MEDIA_ROOT` (0600 files, random UUID names); served only by `student-photo` (permission `view_student_photo`, `Cache-Control: private, no-store`) | `test_access.PrivateMediaTests`, `AccessMatrixTests` | Done. Deployment must not alias `PRIVATE_MEDIA_ROOT` in the web server (documented) |
| SEC-04 | Anonymous server-camera control; `imshow` crashed headless servers | Server camera, OpenCV windows, keyboard wait and audio removed. Browser kiosk sends bounded frames to authenticated, CSRF-protected endpoints | `test_kiosk`, `test_browser`, `test_startup.test_application_code_has_no_server_camera...` | Done. Real webcams and devices not tested (no hardware) |
| SEC-05 | Hard-coded secret, `DEBUG=True`, `ALLOWED_HOSTS=['*']` | Settings package (dev/prod/test). Prod requires a strong `DJANGO_SECRET_KEY` and explicit hosts; HTTPS redirect, HSTS, secure cookies, CSP and Permissions-Policy headers | `test_settings` (incl. `check --deploy --fail-level WARNING`) | **Open (owner):** rotate the old key on existing deployments |
| SEC-06 | Dashboard and attendance public | Every page except login and privacy notice requires login plus a permission; authenticated users without one get 403 | `test_access.AccessMatrixTests` | Done |
| SEC-07 | Unauthenticated, unvalidated enrolment; no consent | Staff-only enrolment (`add_student`), `ModelForm` validation, strict image decoding, exactly one face, recorded consent (status, time, notice version, staff member) | `test_enrollment` | Privacy notice text must be completed by the institution (`templates/privacy_notice.html`) |
| SEC-08 | Vulnerable, end-of-life dependencies | Django 5.2 LTS; Pillow 13; numpy 2.5; `opencv-python-headless` 4.14; PyTorch, torchvision, facenet-pytorch and pygame removed. UTF-8 requirement files, fully pinned runtime graph | `pip-audit -r requirements*.txt` reports no known vulnerabilities (run 2026-11-04); CI runs it | Re-run the audit regularly |
| CV-01 | Embedding/name index misalignment | Embeddings stored one row per student per model version and matched by `student_id`; enrolment requires exactly one face | `test_recognition.GalleryMappingTests`, `test_enrollment` | Done |
| CV-02 | Non-standard preprocessing, raw crops, negative-index wrap | Replaced by OpenCV's documented SFace pipeline (YuNet landmarks → `alignCrop` → `feature`), boxes clamped, box and embedding returned together | `test_real_model` | Done. Accuracy on real faces **not measured** |
| CV-03 | Threshold unjustified; any value accepted | Threshold and margin from validated settings (finite, inside the metric's range). Distance documented as **not** a confidence. Ambiguous best/second-best results are rejected | `test_settings.test_invalid_numeric_configuration`, `test_recognition` | **Open:** calibrate on a consented, labelled dataset. Default 1.128 is OpenCV's published SFace value, not calibrated here |
| CV-04 | One corrupt image disabled recognition for everyone | Photos validated at enrolment; matching never reads photos; invalid gallery entries are skipped and logged by ID | `test_recognition.test_invalid_entries_are_skipped...`, `test_missing_photo_file...` | Done |
| ATT-01 | Wrong date and duplicate rows 00:00–05:29 IST | `timezone.localdate()` throughout; date override removed; `UniqueConstraint(student, date)`; idempotent transactional recording | `test_attendance.LocalDateTests`, `ConstraintTests` | Done |
| ATT-02 | Students resolved by name | Student IDs used throughout; `__str__` includes the registration number | `test_recognition.test_matches_return_student_ids...`, `test_enrollment.test_students_may_share_a_name` | Done |
| PERF-01 | All students re-encoded every frame | Embeddings computed once and stored; in-process gallery cache rebuilt only when a signature query changes | `test_recognition.GalleryCacheTests.test_no_enrolment_photo_is_read_or_re_encoded_per_frame` | Done (measurements in README) |
| UX-01 / AUTH-01 | Admin authorised without seeing the photo | Authorisation page shows the protected photo; authorisation requires consent, a photo and a current template | `test_enrollment.AuthorizationTests` | Done |

## Medium

| ID | Finding | Change | Tests | Status |
|---|---|---|---|---|
| PRIV-01 | Deleting a student left the photo | Photo files deleted after commit on replace, withdrawal and delete; a new file is removed if the transaction fails. "Remove biometric data" withdraws consent and keeps attendance | `test_enrollment.BiometricCleanupTests` | Done. Backups keep old photos until they expire (documented) |
| ATT-03 | Races, no constraint, automatic check-out after 60 s | Explicit Check-in / Check-out modes; `select_for_update` plus unique constraint; repeated events are no-ops | `test_concurrency` (SQLite file DB and PostgreSQL 16) | Done |
| ATT-04 | No corrections or audit trail | Manual entry and correction views with a required reason; `AuditEvent` stores actor, source, before/after times and match distance | `test_attendance.ManualAndCorrectionTests`, `test_kiosk.ConfirmTests` | Done |
| AUTH-02 | Superuser-only roles; staff users loop to login | "Attendance admin" and "Instructor" groups with explicit permissions, synced after `migrate`; 403 page | `test_access`, `test_commands.RolesCommandTests` | Done |
| AUTH-03 | No login throttling; `next` ignored | Django `LoginView` (safe `next`) plus per-username and per-address throttling | `test_auth.LoginThrottleTests`, `LoginLogoutTests` | Per-address limit uses `REMOTE_ADDR` (proxy caveat documented) |
| AUTH-04 | Logout via GET | `LogoutView` is POST-only, with CSRF protection | `test_auth` | Done |
| VAL-01 | Camera-config 500s and misleading errors | Camera configuration removed (migration 0014 drops the table); kiosk limits come from validated settings | `test_settings` | Done |
| VAL-02 | Invalid report date caused 500 | Filter form validation with errors shown on the page | `test_attendance.ReportTests` | Done |
| VAL-03 | `"false"` authorised a student | Strict choice field (`authorize` / `revoke`) | `test_enrollment.AuthorizationTests.test_strict_decision_values` | Done |
| SSRF-01 | Arbitrary camera source URLs | Server-side camera sources removed entirely | `test_startup` | Done |
| CV-05 | No liveness / presentation-attack defence | Every match needs staff confirmation; kiosk text warns about photos and screens | — | **Open:** no liveness detection. Multi-frame consistency would not be liveness and was not claimed |
| CV-06 | Labels drawn on the wrong faces | Kiosk accepts exactly one face per frame; box and embedding kept together | `test_kiosk.RecognizeStateTests.test_unsuitable_frames` | Done |
| CV-07 | Models loaded and downloaded at import | Lazy engine; explicit `setup_face_models` from a pinned commit with SHA-256 verification; `check` and `migrate` do not import OpenCV | `test_startup`, `test_commands.SetupFaceModelsTests` | Done |
| DB-01 | Missing constraints and indexes; `CASCADE` loses history | Unique registration number, unique `(student, date)`, check constraint, indexes, `PROTECT`, choices for branch and year | `test_attendance.ConstraintTests`, `test_migrations` | Upgrade preflight blocks on conflicts and never merges data |
| DB-02 | N+1 report, no pagination | `select_related`; pagination (25/50); constant query count | `test_attendance.ReportTests` (`assertNumQueries`) | Done |
| DEP-01 | Wrong Python claim; no setup steps | README documents Python 3.12–3.13 (both tested), migrate, createsuperuser and setup_face_models | — | Done |

## Low

| ID | Change |
|---|---|
| L-01 | Dead `forms.py` (non-existent `UploadedImage`) replaced by real forms |
| L-02 | Duplicate and unused imports removed; `ruff` lint in CI |
| L-03 | `suc.wav` and audio playback removed |
| L-04 | Template `DIRS` is `BASE_DIR / "templates"` only |
| L-05 | Committed bytecode removed and git-ignored |
| L-06 | Unused and obsolete templates removed |
| L-07 | Broken Font Awesome kit and all CDN assets removed; one local stylesheet |
| L-08 | Invalid CSP `<meta>` replaced by real CSP and Permissions-Policy headers |
| L-09 | Dashboard uses server date and time |
| L-10 | Admin is read-only (changes go through audited views) |
| L-11 | `print()` replaced by `logging`, which logs IDs only, never personal data |

## New findings during remediation

| ID | Finding | Change | Tests |
|---|---|---|---|
| DB-03 | `Student.year` was `max_length=7`, but the form posted `"1st Year"` (8 characters). SQLite ignores the limit; **PostgreSQL rejected every enrolment** | Widened to 16 in migration 0014 | `test_migrations` (legacy values per backend); full suite on PostgreSQL 16 |
| DB-04 | Migration `0004` re-added a column that `0001` already creates, so **fresh installs on PostgreSQL failed** | `0004` keeps its state change but skips the redundant database operation (`SeparateDatabaseAndState`). Already-migrated databases are unaffected | Full migration chain and suite on PostgreSQL 16 |

## Not done by design

- No attendance percentages, absence or lateness: the system has no timetable or session data to
  compute them correctly.
- No unsupervised automatic attendance: without liveness detection, a person must confirm each match.
- No course/session management platform (out of scope).
