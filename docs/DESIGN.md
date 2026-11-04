# Interface design notes

## Direction

- **Product.** A daily attendance register for staff: enrol students, confirm face-matched
  check-ins, record manual entries, and report.
- **Character.** Calm, exact, accountable.
- **World.** The paper attendance register on a front desk: ruled columns, blue-black register
  ink, a time stamp.
- **Moments.**
  - The *Today's register* panel: one stacked bar of the day's statuses and a ruled 06:00–20:00
    timeline of check-ins against the expected start and grace period.
  - The kiosk confirmation: the enrolment photo beside a stamped server time.
- **Signature.** Every attendance time is set as a small ruled stamp in IBM Plex Mono with tabular
  figures, next to a single status dot.

## System

| | |
|---|---|
| Surfaces | Neutral graphite ramp for light and dark themes |
| Accent | One register-ink accent (`#2b4c8c`, light theme) |
| Status colours | Muted green, amber and red, plus slate for leave, each always with a text label |
| Type | IBM Plex Sans for the interface and IBM Plex Mono for times and codes, self-hosted (`static/app1/fonts`, SIL Open Font License) |
| Weights | 400 and 500, with 600 at most |
| Layout | 4 px spacing grid; 248 px sidebar on desktop, a menu button below 860 px; tables scroll inside their own frame |
| Radii | 8 px for controls, 12 px for panels |
| Accessibility | Visible focus rings, a skip link, labelled controls |
| Motion | `prefers-reduced-motion` is respected |
| Assets | One stylesheet (`static/app1/app.css`), no framework and no CDNs |
| CSP | Strict (`style-src 'self'`), so charts are SVG positioned with attributes, never inline styles |
| Print | Hides navigation and controls, so reports and the daily register print as plain tables |

## Process and credit

The redesign followed the **unslop** skill by Damir Sokolovsky (@heydamir,
https://github.com/heydamir/unslop), licensed CC BY 4.0, at revision
`a6f121eee5f29c2f74976b75456feae8b66afb3a`. The skill's tokens and checklist guided:

- spacing, the neutral ramp and the single accent;
- sentence-case labels and plain copy;
- one bright moment per screen;
- named transitions only.

The skill itself is not part of this repository.

Screenshots in `docs/screenshots` are produced by `SCREENSHOT_DIR=docs/screenshots python manage.py
test --tag screenshots`. They use invented people and drawn silhouettes; the `before-*` images
show the previous interface with the same kind of synthetic data.
