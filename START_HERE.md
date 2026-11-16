# Start here: TenderPilot

TenderPilot helps a bid-submission company prepare tenders for its client firms: study a tender, check eligibility against the bidding firm's records (or the JV partners' records), prepare the forms and the priced BOQ, and assemble the files to upload to the portal. It runs on your own computer, and your documents stay on it.

## 1. Install (recommended)

The `installer` folder holds two setup files:

| File | What it installs |
|---|---|
| `TenderPilot-Setup.exe` | TenderPilot for real tenders, plus a "TenderPilot Demo" shortcut and a backup shortcut |
| `TenderPilot-Demo-Setup.exe` | The demo only: a practice copy with made-up (SYNTHETIC) data, for learning or showing others |

Double-click the file and follow the steps. No administrator password is needed. Windows may show "Windows protected your PC" because the file is not code-signed: click **More info**, then **Run anyway**.

After installing, open the Start menu and click **TenderPilot** (or **TenderPilot Demo**). A small black window opens and your browser shows TenderPilot. Keep the window open while you work; close it to stop.

- Real data is kept in `%LOCALAPPDATA%\TenderPilot`; demo data in `%LOCALAPPDATA%\TenderPilot-Demo`. Uninstalling never deletes them.
- Scanned-tender reading (OCR) is built in. Old .doc/.xls files also need LibreOffice (free).
- The installed version starts with an empty database. Tenders entered in the developer copy (the method below) are not moved across automatically.

## 1b. Start from the source folder (developers)

1. Open the `TENDERPRO` folder in File Explorer.
2. Right-click `scripts\start.ps1` and choose **Run with PowerShell**.
   (Or open PowerShell in this folder and type: `powershell -ExecutionPolicy Bypass -File scripts\start.ps1`)
3. The first start takes a few minutes while it installs things. Your browser then opens at **http://127.0.0.1:8000**.
4. Keep the PowerShell window open while you work. Close it to stop TenderPilot.

**First time only:** the sign-in page asks you to create your organization and an administrator account. Use a password of at least 10 characters.

**Want to try it with sample data first?** Run `scripts\start.ps1 -demo`. This creates a made-up company and tender, all marked SYNTHETIC, and prints the demo sign-in details in the PowerShell window.

## 2. A normal tender, step by step

| Step | Where | What you do |
|---|---|---|
| 1 | Client firms | Add each firm you bid for, once: profile, then its documents (turnover, completion certificates, PAN, GST, registrations, machinery, personnel). Type values exactly as printed; a reviewer clicks **Verify**. The library is reused for every tender. |
| 2 | Tenders, then New tender | Enter the name of work and tender ID, and upload **all** tender volumes and corrigenda. |
| 3 | Documents tab | Wait for processing. Every page is listed as processed, unreadable, duplicate and so on. Choose a type for any file marked "Choose type". |
| 4 | Clauses tab | Click each requirement to see the highlighted source page. Confirm it, correct the value, or reject it. |
| 5 | Command centre | Under **Bidding firm(s)** choose **Single firm** or **Joint venture** and pick the firm(s) from the library (lead partner and shares for a JV). Enter the qualification cutoff date from the tender clause. |
| 6 | Eligibility tab | Confirm each rule, then click **Evaluate now**. The table shows pass/fail with the full working and what is missing. |
| 7 | Checklist tab | Link a vault document to each item and accept it. |
| 8 | Document studio | Generate cover letters and forms. Print and sign them (or use your DSC), then upload the signed PDF. |
| 9 | BOQ and quote | Import the department's BOQ, enter basic rates with their source, build up each rate, and freeze a quote. The estimator reviews it and a different person approves it. Then write the rates into the official workbook. |
| 10 | Envelopes and export | Create one upload slot per portal field, add files to each, run **preflight**, build the final package, and have the approver freeze it. |
| 11 | Portal (outside TenderPilot) | Upload the files and sign, pay and submit on the portal yourself. Then record the acknowledgement number and file in TenderPilot. |

## 3. Things TenderPilot will never do

- Submit the bid, sign with your DSC, pay fees, or enter OTPs or CAPTCHAs.
- Create, alter or "fill in" certificates, bank guarantees, seals or signatures.
- Treat an unknown as a pass, or check certificates against today's date instead of the tender's cutoff date.
- Send your documents to an outside AI service unless an administrator turns it on in Settings.

## 3b. One server for the whole office

Install TenderPilot once on a server and tick "Let other computers in the office use TenderPilot" in the installer
(or turn it on later under **Settings & audit → Network access**). Staff then open the address shown there, such as
`https://SERVERNAME:8471`, in their own browser; nothing is installed on their computers. Full steps:
`packaging/INSTALL-ON-SERVER.txt` (also inside the setup zip).

## 4. Updates

When a new version is published, administrators see **Update available** in the menu. Open **Settings & audit → Software update**, read
what is new, click **Download update**, then **Back up my data and install**. TenderPilot saves a full backup first, closes, installs and
starts again by itself in about a minute. Your tenders and documents are kept. Ask other users to save their work before you install.

## 5. Optional extras

- **Scanned tenders and old Word/Excel files:** Tesseract OCR (English and Hindi) and LibreOffice are installed on this computer. Check they are working under **Settings & audit → System checks**.
- **AI reading help (optional, paid):** under **Settings & audit → AI provider**, an administrator can connect an Anthropic account. Type the API key only into that screen, never into chat or email. AI suggestions are marked "AI interpretation", show the clause they came from, and must be checked by a person. Amounts, dates and eligibility limits always come from the rule-based reading and your reviewers, never from AI.
- **Backups:** stop TenderPilot, then run `scripts\backup.ps1`. It writes a zip into the `backups` folder.

If something goes wrong, the screen tells you what happened and how to fix it. For technical details, see `README.md`.
