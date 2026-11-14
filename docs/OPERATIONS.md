# Operating guide

## Deployment options
1. **Single Windows PC (default):** `scripts/start.ps1`. SQLite plus the local file store in `backend/data/`. Suitable for one office. Bind address 127.0.0.1 only.
2. **Office server:** run uvicorn behind IIS/nginx with HTTPS. Set `SECRET_KEY` and `DATABASE_URL` (PostgreSQL), set `CORS_ORIGINS` to your hostname, and restrict access to your LAN or VPN.
3. **Containers:** `docker-compose.yml` (PostgreSQL plus app with Tesseract). Written but untested on the build machine; verify before relying on it.

Never expose TenderPilot to the public internet without HTTPS, a reverse proxy and network restrictions.

## Windows installer
`packaging\build.ps1` builds `installer\TenderPilot-Setup.exe` and `installer\TenderPilot-Demo-Setup.exe` (PyInstaller folder build plus Inno Setup; Tesseract and the eng/hin/osd data are bundled, LibreOffice is not). Installs go per user into `%LOCALAPPDATA%\Programs`; data goes into `%LOCALAPPDATA%\TenderPilot[-Demo]`. The app listens on 127.0.0.1:8471 (demo 8472) and moves to a free port if that one is taken by another program. `TenderPilot.exe --backup` takes a consistent backup (SQLite online backup) even while the app runs. The installers are not code-signed, so SmartScreen warns on first run.

## Backup and restore
- SQLite: stop the server, run `scripts/backup.ps1`, and copy the zip off the machine. To restore, stop the server and unzip into `backend/data/`.
- PostgreSQL: `pg_dump` the database and back up the file volume (`STORAGE_DIR`) at the same time. Files are referenced by key from the database.
- Test a restore every quarter.

## Data retention
`Organization.retention_days` is stored (Settings, via API). Automatic purge is **not yet implemented**; delete closed tenders' data manually per your policy.

## External services and cost assumptions
| Item | Default | Cost note |
|---|---|---|
| AI interpretation (Anthropic) | Off | Billed by Anthropic per token. List prices cached 2026-10-06, USD per million tokens (input / output): Opus 5.5 4 / 20, Sonnet 5.5 2 / 10, Haiku 5.5 0.10 / 0.50. Settings shows a per-100-page estimate and a per-document estimate before anything is sent, and enforces a monthly budget. Check anthropic.com/pricing before relying on these figures. |
| OCR (Tesseract) | Installed, free | CPU time only (about 1 s per page) |
| LibreOffice | Installed, free | About 2–4 s per legacy file |
| Hosting | Local PC | Server or cloud cost depends on your provider |

## Integrations
Settings & audit → **System checks** runs a real self-test for each dependency and says what to do when one fails. Use **Run checks again** after installing or changing anything.

- **OCR:** Tesseract 5.4 at `C:\Program Files\Tesseract-OCR`. Language data lives in `backend\data\tessdata` (eng, hin, osd) because the Program Files folder is not writable without admin rights; override with `TESSDATA_DIR`. Keep `OCR_LANGUAGES=eng+hin` in that order: `hin+eng` dropped digits from rupee amounts in testing.
- **Legacy .doc/.xls:** LibreOffice, run headless with a private profile in `backend\data\lo_profile`. The converted copy is stored separately; the original upload is never modified. Override the path with `SOFFICE_CMD`.
- **AI provider:** an org admin opens Settings → AI provider, chooses a model, pastes an Anthropic API key (it is encrypted with Windows DPAPI for the account running the server and only the last 4 characters are ever shown), clicks **Test connection** (free: it reads model metadata, no tokens), then ticks consent. Changing the key, model or provider withdraws consent. AI review is started per document from the Documents tab, after a cost estimate. Moving the database to another Windows account or machine makes the stored key unreadable: enter it again.

## Troubleshooting
- *Scanned pages show "unreadable":* open Settings → System checks. If OCR is not "Working", follow its instruction, then retry the job in Documents → Processing queue.
- *"Workbook cannot be round-tripped safely":* the BOQ has macros, links, charts or locked rate cells. Use the manual entry mapping shown under the quote.
- *409 "changed by someone else":* reload the page; another user saved first.
