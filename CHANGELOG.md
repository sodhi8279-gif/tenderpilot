# TenderPilot changes

Each section is shown in the app (Settings & audit > Software update) when that version is offered as an update.
Raise APP_VERSION in backend/app/version.py and add a section here for every release.

## 1.2.0
- Software update from the app: Settings & audit > Software update checks for a new version, downloads it, backs up all
  your data and installs it. TenderPilot restarts by itself; your tenders and documents are kept.
- Bidder panel explains an empty company list and links to the Company vault.

## 1.1.0
- Order of precedence: suggested ranks from the tender's clause; conflicts resolved by a reviewer with one click.
- Editable quote scenarios (trades, delay, payment cycle, retention, interest).
- Company vault: clean up scanned documents (rotate, crop, straighten) into a separate copy.
- Tender forms supplied as PDF: fillable PDFs filled in; scanned forms filled at the positions you click.
- Tender reader: personnel, machinery and quantity tables continued on the next page are read.
- Hindi screen labels (English / हिन्दी switch).
- Safety: the database is copied before every update; old tenders are re-read only when you ask.
