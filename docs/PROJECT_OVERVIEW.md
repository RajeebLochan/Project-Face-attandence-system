# Project Overview

This sanitised overview replaces the earlier `Docomentation.docx`. That file was removed
because its screenshots contained students' personal data.

## Problem

Manual roll calls and paper registers take class time, are error-prone and allow proxy
attendance. This project records attendance with face recognition, while keeping a person in
control and a manual alternative available.

## Objectives

- Record check-in and check-out for enrolled students who have consented to face recognition.
- Keep a staff member in the loop: every recognition is a *candidate* that a person confirms.
- Provide manual attendance and audited corrections for everyone, including students who
  decline face recognition.
- Protect biometric data: private storage, consent records, role-based access and deletion on
  request.

## Scope

- One Django web application, served over HTTPS. A browser on a laptop or tablet with a
  webcam acts as the kiosk. The server needs no camera, display or audio device.
- Roles: **Attendance admin** (enrolment, authorisation, data removal, audit log) and
  **Instructor** (kiosk, manual attendance, corrections).
- Not in scope: timetables, courses, absence or lateness calculation, student self-service,
  and unsupervised or liveness-checked recognition.

## Architecture

```
Browser (kiosk page)                    Django server
--------------------                    -------------------------------------------
getUserMedia preview  --JPEG frame-->   /kiosk/recognize/  (login + permission + CSRF,
one frame in flight                      rate limit, size limits, one analysis slot)
                                          -> decode + validate image (Pillow)
                                          -> YuNet detect, SFace alignCrop + embedding (OpenCV)
                                          -> match against cached gallery (by student ID)
                      <--candidate----    signed, short-lived token + name + protected photo URL
staff confirms        --token------->   /kiosk/confirm/ -> transactional, idempotent record
                                          + audit event (actor, source, match distance)
```

Main modules:

| Path | Purpose |
|---|---|
| `Project101/settings/` | Environment-driven dev, prod and test settings |
| `app1/models.py` | `Student`, `FaceEmbedding`, `Attendance`, `AuditEvent` |
| `app1/recognition/` | Lazy face engine, pinned model files, embedding gallery, identification |
| `app1/services/` | Image validation, attendance recording, student lifecycle (photo, consent, deletion) |
| `app1/views/` | Auth, dashboard, students, attendance, reports, kiosk, notifications, administration, error pages |
| `app1/notifications/` | Outbox enqueueing, recipient rules, delivery worker, daily digest, address verification |
| `app1/scope.py` | Which students and records each staff member may see (batch assignments) |
| `deploy/`, `Dockerfile`, `docker-compose.yml` | Production deployment with PostgreSQL, gunicorn, worker, scheduler and Caddy |
| `app1/management/commands/` | `setup_face_models`, `rebuild_face_embeddings`, `attendance_preflight`, `relocate_legacy_photos`, `sync_roles`, `run_notification_worker`, `send_daily_digest` |
| `templates/`, `static/app1/` | One base template, one stylesheet, kiosk and photo-capture scripts |

## Technology

Python 3.12/3.13, Django 5.2 LTS, OpenCV (headless) with the YuNet detector and SFace
recogniser from the OpenCV Model Zoo, Pillow, numpy. SQLite works for a single host;
PostgreSQL is supported and recommended for several workers.
