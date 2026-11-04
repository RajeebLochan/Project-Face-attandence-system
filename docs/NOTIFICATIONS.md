# E-mail notifications

## What is sent, and to whom

| Event | Default | Sent to |
|---|---|---|
| Check-in, check-out | on, once notifications are switched on | staff who opted in and can see the student; the student and/or guardian if enabled for that student |
| Correction, or an entry for an earlier day | off | the same people, if "corrections and past entries" is enabled |
| Daily summary | off | staff who opted in |
| Address verification | when staff request it | the address being verified |

Rules:

- Kiosk candidates that nobody confirmed never send anything.
- A same-day manual entry for a time that has already passed says when it was entered.
- Entries for an earlier day never pretend to be live check-ins.

## Switching it on

1. Configure SMTP in the environment (below) and restart the web and worker containers.
2. In *Settings*, enter the institution name and the contact e-mail. *Send test e-mail* sends one
   message straight to your own address (at most once a minute) and shows the server's answer.
3. Verify recipients. Each address must be confirmed through a link before it receives anything:
   - Staff: on the *Staff* page, pick a person, choose what they receive, and press *Send
     verification e-mail*.
   - Students and guardians: on the student's page, add the guardian's address, tick who should
     be told, and send the verification e-mails.
4. Tick *Send attendance e-mails* in *Settings*. The form refuses if something required is
   missing.

Changing an address removes its verified status. Queued messages for an address, preference or
access that is no longer valid are cancelled.

## SMTP settings

| Variable | Example | Notes |
|---|---|---|
| `EMAIL_HOST` | `smtp.example.com` | Empty means e-mail is not configured |
| `EMAIL_PORT` | `587` | 587 with STARTTLS, or 465 with SSL |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | `true` / `false` | Set exactly one; both together refuses to start |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | | Only in `.env`; never shown in the interface or logs |
| `DEFAULT_FROM_EMAIL` | `Attendance <attendance@example.edu>` | Required in production when `EMAIL_HOST` is set |
| `EMAIL_TIMEOUT` | `15` | Seconds, 1–120 |
| `SITE_URL` | `https://attendance.example.edu` | Used to build verification and staff record links |

## Sender domain: SPF, DKIM and DMARC

Without these records, many receivers put the messages in spam or reject them. Your mail provider
documents the exact values for your account; this guide does not invent them.

- **SPF.** Add the provider's include to your domain's SPF TXT record, so it may send for your
  domain.
- **DKIM.** Publish the public key(s) the provider gives you, usually one or two CNAME or TXT
  records, and switch on signing in the provider's console.
- **DMARC.** Publish a `_dmarc` TXT record. Start with a monitoring policy (`p=none`) and a report
  address, then tighten it once reports show your mail passes.
- **Sender address.** Send from an address on the domain you configured, matching
  `DEFAULT_FROM_EMAIL`.

## Running the worker

```bash
python manage.py run_notification_worker              # long-running
python manage.py run_notification_worker --once       # one pass, for cron or debugging
python manage.py run_notification_worker --healthcheck  # exit 0 if a worker reported in recently
```

The Compose stack runs the worker as the `worker` service, which restarts automatically.

| Status | Meaning |
|---|---|
| Queued | Waiting, or waiting for its next retry |
| Processing | Claimed by a worker |
| Accepted by mail server | Your SMTP server took it. This is not proof of inbox delivery |
| Failed | Rejected permanently (for example an unknown recipient), or five temporary failures in a row |
| Cancelled | The recipient or permission stopped being valid, or staff cancelled it |

**Retries.** Temporary failures (timeouts, disconnects, 4xx replies) are retried after 1, 5, 15 and
60 minutes. A message left in Processing by a crashed worker is requeued after
`NOTIFICATION_LEASE_SECONDS` (default 300).

**Duplicates.** A crash between "the server accepted it" and "we recorded that" means the message
may arrive twice. SMTP cannot avoid this; the application does not promise exactly-once delivery.

## Daily summary

1. Switch it on in *Settings* and pick a time.
2. Choose which staff receive it on the *Staff* page.

The `scheduler` service runs `send_daily_digest --if-due --loop 300`. Without Compose, a cron
entry works too:

```cron
*/10 * * * * cd /srv/attendance && .venv/bin/python manage.py send_daily_digest --if-due
```

Each address gets at most one summary per day, even if the command runs repeatedly or on two hosts.

## Local testing without sending real mail

- **Development:** messages are written to `var/sent-mail/` by default.
- **Mailpit:** run `docker compose --profile mailtest up -d mailpit`, set `EMAIL_HOST=mailpit`
  (or `localhost`), `EMAIL_PORT=1025` and `EMAIL_USE_TLS=false`, then read messages at
  <http://127.0.0.1:8025>.
- **Tests:** they use Django's in-memory backend and never contact a mail server.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Nothing leaves the queue | Is a worker running? The Notifications page shows when it last reported in. `docker compose logs worker` |
| Everything fails with connection errors | Host, port, TLS/SSL choice and firewall. Try *Send test e-mail* |
| Authentication errors | Username and password; some providers need an app password or an allowed sender |
| Accepted but never arrives | Spam folder, SPF/DKIM/DMARC, and your provider's delivery logs |
| A recipient gets nothing | Is the address verified (staff page or student page)? Is that event type switched on? Can that staff member see the student's batch? |
| Messages show Cancelled | The detail page gives the reason, for example "address changed" |
