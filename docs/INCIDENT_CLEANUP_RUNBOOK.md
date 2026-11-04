# Incident Cleanup Runbook: Historical Exposure of Credentials and Personal Data

**Status: OPEN.** This runbook is a plan for the repository owner. Nothing below has
been executed. Removing files from the latest commit does **not** remove them from
Git history, from GitHub's caches, or from clones and forks that already exist.
**Do not treat the exposure as resolved** until the owner has completed the
owner-only steps and accepted the residual risk recorded in section 7.

## 1. What was exposed

The previous public versions of this repository contained:

| Item | Where (path in history) | Sensitivity |
|---|---|---|
| A working Django superuser username and password | `README.md` (an "Admin Login Credentials" section) and the matching password hash in `db.sqlite3` | Credential |
| The Django `SECRET_KEY` | `Project101/settings.py` | Secret |
| Enrolment photographs of identifiable people | `media/students/*.jpg` | **Biometric / personal data** |
| Student records (names, e-mail addresses, registration numbers, branch, year) and attendance rows | `db.sqlite3` (`app1_student`, `app1_attendance`, `django_admin_log`) | Personal data |
| Active and expired session keys, admin log entries | `db.sqlite3` (`django_session`, `django_admin_log`) | Session data / personal data |
| Screenshots showing a student's name, e-mail, registration number and face | `Docomentation.docx` | Personal data |

To list the exact commits on your machine (the commit IDs are deliberately not
repeated here):

```bash
git log --all --format='%h %ad %s' --date=short -- README.md Project101/settings.py db.sqlite3 media Docomentation.docx
```

The remediation branch removes these files from the current tree, replaces the
credentials section with `createsuperuser` instructions, and moves the secret key to
environment variables. **That is all a commit can do.**

## 2. Owner-only actions: credentials and secrets (do first, today)

These need someone with access to the running systems. No automated agent should
perform them.

1. **Change the published administrator password** on every instance that was ever
   set up from this repository, including laptops and lab machines that copied
   `db.sqlite3`. Preferably **deactivate that account** and create a new
   administrator with `python manage.py createsuperuser`.
2. If that password was reused anywhere (e-mail, institution portals, other apps),
   **change it there too**. Assume it is known to the public.
3. **Rotate `SECRET_KEY`** on every deployment. The old key is public, so anyone can
   forge signed values (sessions, password-reset tokens, signed cookies) for an
   instance that still uses it. Generate a new key:
   ```bash
   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
   ```
   Put it in the `DJANGO_SECRET_KEY` environment variable (see `.env.example`).
   Rotating the key logs everyone out, which is intended.
4. On any instance that is still running, review `django_admin_log` and the web
   server logs for logins or changes you do not recognise, and keep a record of
   what you find.

## 3. Owner-only actions: coordinate before rewriting history

A history rewrite changes every commit ID and breaks every existing clone, branch
and pull request.

1. **Announce a freeze** to all collaborators: no pushes until the rewrite is done.
2. List the open branches and pull requests, including this remediation branch.
   After the rewrite, each must be rebased onto the rewritten `main` or recreated.
   Old PR refs (`refs/pull/*`) cannot be rewritten by you (see section 5).
3. **Make a private backup** of the current repository (`git clone --mirror`) and
   store it somewhere access-controlled, because it contains the personal data.
   Delete it once the institution's retention decision allows.
4. Optional, owner decision: temporarily set the repository to **private** to
   reduce further exposure while cleanup is in progress. This does not recall
   copies that already exist.

## 4. Owner-only actions: rewrite history

Use [`git filter-repo`](https://github.com/newren/git-filter-repo), as recommended
by GitHub's "Removing sensitive data from a repository" documentation.

```bash
# 1. Fresh mirror clone (do not reuse a working copy)
git clone --mirror https://github.com/RajeebLochan/Project-Face-attandence-system.git cleanup.git
cd cleanup.git

# 2. Remove the personal-data files and bytecode from every commit
git filter-repo --invert-paths \
  --path db.sqlite3 \
  --path media/ \
  --path Docomentation.docx \
  --path-glob '*/__pycache__/*'

# 3. Redact the old password and old SECRET_KEY wherever they appear in text.
#    Put each literal value on its own line of a private file, e.g.
#        <old password>==>REDACTED
#        <old secret key>==>REDACTED
#    Keep that file outside the repository and delete it afterwards.
git filter-repo --replace-text /secure/location/replacements.txt

# 4. Verify nothing remains (each command should print nothing)
git log --all --format=%h -- db.sqlite3 media Docomentation.docx
git grep -I -n -e 'Admin Login Credentials' $(git rev-list --all) | head

# 5. Force-push the rewritten history (owner only; overwrites GitHub)
git push --force --mirror origin
```

After the push:

- Every collaborator must **delete their old clone and re-clone**. Merging an old
  clone back reintroduces the data.
- Rebase or recreate open branches and PRs, including the remediation branch, on
  the rewritten `main`.
- Delete stale branches and tags that still point to old commits.

## 5. Owner-only actions: GitHub cached content and references

Even after a force-push, GitHub can keep serving old commits by their SHA, through
pull-request refs, and through cached views.

1. Contact **GitHub Support** using the *Removing sensitive data* process. Provide the
   repository name and the list of affected commit SHAs (from section 1) and ask
   them to:
   - remove cached views of those commits,
   - dereference or garbage-collect the old pull-request refs.
2. Check the repository's **forks** (Insights → Forks). You cannot delete other
   people's forks. You may ask fork owners to delete them. For personal data,
   GitHub's *Private Information Removal Policy* may apply; whether to file a
   request is the owner's and institution's decision.
3. Check third-party archives (for example Software Heritage or web archives) and
   search-engine caches, and request removal where their policies allow.

## 6. Owner and institution decisions: people affected

- Inform the institution's **data protection contact** about the exposure of
  biometric and personal data. Under India's Digital Personal Data Protection Act,
  2023 (and GDPR, if it applies), the institution decides whether notification of
  the affected people or a regulator is required, and how.
- If any affected person was under 18, involve their parent or guardian as the
  institution's policy requires.
- **No one has been contacted as part of this remediation.** Contacting people is
  outside an automated agent's authority.

## 7. Residual risk (cannot be eliminated)

- Clones, forks, downloads and archives made while the data was public **cannot be
  recalled**. Treat the exposed photographs and records as permanently public.
- The old password and secret key must be treated as compromised forever, even
  after rotation.
- Record in the institution's incident log what was exposed, from when to when
  (see `git log` dates), what was done, and who approved the residual risk.

## 8. Checklist

| Step | Owner | Done? |
|---|---|---|
| Admin password changed or account deactivated on all instances | Repository owner / sysadmin | ☐ |
| Reused passwords changed | Account holder | ☐ |
| `SECRET_KEY` rotated on all deployments | Sysadmin | ☐ |
| Logs reviewed for unauthorised access | Sysadmin | ☐ |
| Collaborators notified and pushes frozen | Repository owner | ☐ |
| Private mirror backup stored (and later deleted) | Repository owner | ☐ |
| History rewritten and force-pushed | Repository owner | ☐ |
| Collaborators re-cloned; branches/PRs rebased | Collaborators | ☐ |
| GitHub Support request filed (cached views, PR refs) | Repository owner | ☐ |
| Fork owners asked / removal requests filed | Repository owner | ☐ |
| Data protection contact informed; notification decision recorded | Institution | ☐ |
| Residual risk accepted and recorded | Institution | ☐ |
