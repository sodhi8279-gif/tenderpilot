# Operating guide

## Deployment options
1. **Single Windows PC (default):** `scripts/start.ps1`. SQLite plus the local file store in `backend/data/`. Suitable for one office. Bind address 127.0.0.1 only.
2. **Office server:** run uvicorn behind IIS/nginx with HTTPS. Set `SECRET_KEY` and `DATABASE_URL` (PostgreSQL), set `CORS_ORIGINS` to your hostname, and restrict access to your LAN or VPN.
3. **Containers:** `docker-compose.yml` (PostgreSQL plus app with Tesseract). Written but untested on the build machine; verify before relying on it.

Never expose TenderPilot to the public internet without HTTPS, a reverse proxy and network restrictions.

## Backup and restore
- SQLite: stop the server, run `scripts/backup.ps1`, and copy the zip off the machine. To restore, stop the server and unzip into `backend/data/`.
- PostgreSQL: `pg_dump` the database and back up the file volume (`STORAGE_DIR`) at the same time. Files are referenced by key from the database.
- Test a restore every quarter.

## Data retention
`Organization.retention_days` is stored (Settings, via API). Automatic purge is **not yet implemented**; delete closed tenders' data manually per your policy.

## External services and cost assumptions
| Item | Default | Cost note |
|---|---|---|
| AI extraction | Off | If enabled, cost = pages × tokens per page × provider price. Configure `AI_MAX_TOKENS`; check the provider's current price list. No fixed figure is assumed. |
| OCR (Tesseract) | Optional, free | CPU time only |
| Hosting | Local PC | Server or cloud cost depends on your provider |

## Troubleshooting
- *Scanned pages show "unreadable":* install Tesseract and set `TESSERACT_CMD`, then retry the job in Documents → Processing queue.
- *"Workbook cannot be round-tripped safely":* the BOQ has macros, links, charts or locked rate cells. Use the manual entry mapping shown under the quote.
- *409 "changed by someone else":* reload the page; another user saved first.
