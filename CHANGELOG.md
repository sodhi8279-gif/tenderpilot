# TenderPilot changes

Each section is shown in the app (Settings & audit > Software update) when that version is offered as an update.
Raise APP_VERSION in backend/app/version.py and add a section here for every release.

## 1.6.0
- **Install an update from your own computer.** Settings & audit > Software update > "Install from a setup file
  on your computer": an administrator chooses TenderPilot-Setup.exe (or the release zip) downloaded to their own
  computer, types their password, and uploads it. No need to sign in to the server.
- Before anything is installed TenderPilot checks the file: it must be a TenderPilot installer, newer than the
  installed version, and, when the server has internet, identical to the one published for that version (or to
  the checksum in the zip). Anything else is refused and deleted. As with downloaded updates, a full backup is
  written first; every upload is recorded in the audit log.
- Installers now carry their name and version, which is what the check reads; upload works for installers from
  1.6.0 on (for this one update, install 1.6.0 on the server, or use the Download update button).

## 1.5.0
- **Client firms: upload documents and TenderPilot reads them for you.** On a firm's page, choose one or more
  documents (PDF, scan, photo, Word or Excel): GST and PAN, incorporation certificate, Udyam, EPF/ESI, contractor
  registration, work orders, completion certificates, CA turnover certificates, ISO, cancelled cheque. TenderPilot
  recognises the kind of document and fills in its dates and values (work name, executed value, completion date,
  turnover for each financial year ...). Check them, then save: records stay unverified until a reviewer checks them.
- Firm details found in the documents (PAN, GSTIN, CIN, Udyam, address, bank account, IFSC ...) are listed next to
  what is recorded now, with the line they were read from. Only ticked lines are added; a value already recorded is
  never replaced unless you tick it.
- **Add client firm > Fill from documents** fills the empty boxes of a new firm from its certificates, and can keep
  the documents in the firm's records.
- "Add a document" fills its empty boxes from the chosen file.
- Scans and photos are read with the built-in OCR; GSTINs misread on a scan are corrected only when the GSTIN
  check digit confirms it. Documents are read on your server: nothing is sent outside.

## 1.4.1
- **Restart TenderPilot now** (Settings & audit > Network access) works: TenderPilot starts again in its own window.
  In 1.4.0 it stopped and showed "Windows cannot find '\\'"; start it from its icon if that happened.
- New switch in Network access: **Start TenderPilot by itself when this Windows account signs in** (for servers).
- Update check works on freshly installed Windows servers: TenderPilot now trusts its own up-to-date certificate list
  as well as Windows' store. When a check fails, the message says why (no internet, firewall, certificate problem).
- After an update, browsers always load the new screens (the page is no longer kept in the browser cache).
- The harmless "fitz FutureWarning" line no longer appears in the TenderPilot window.

## 1.4.0
- **Use TenderPilot from every computer in the office**: install it once on a server and turn on
  Settings & audit > Network access. Staff open the address shown there in their own browser. Connections are
  encrypted (HTTPS) with a certificate TenderPilot makes for the server; the panel shows the Windows Firewall step and
  lets staff computers download the certificate to remove the browser warning.
- Installer options for servers: start TenderPilot when Windows signs in, and allow other computers.
- Staff accounts: administrators can switch an account off or on and reset a forgotten password; everyone can change
  their own password (Settings > Your account).
- Security: repeated wrong passwords block sign-in for 15 minutes; the first organisation can only be created on the
  server itself; the demo is never opened to the network.

## 1.3.0
- Built for a company that submits tenders for other firms: "Company vault" is now **Client firms**.
- Each client firm has its own page: full profile (CIN, Udyam, EPF/ESI, contacts, bank for EMD refunds) and its documents
  grouped as Financials, Work experience, Machinery, Personnel, Registrations & certificates, with expired and expiring
  documents flagged and turnover by year shown.
- On a tender, choose **Single firm** or **Joint venture** and pick the firms from the library (lead partner and shares).
  Their credentials appear at once and are used for eligibility, the checklist and the forms; nothing is typed again.
- Tender list: filter by client firm. Settings: **Your company** (your own details, usable in forms).
- Forms can fill firm details (Udyam, EPF, ESI, e-mail, bank) and your company's details.
- Existing firms, documents and tenders are unchanged (update 0006 adds two empty profile columns).

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
