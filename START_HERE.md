# Start here: TenderPilot

TenderPilot helps your team study a tender, check eligibility against your own company records, prepare the forms and the priced BOQ, and assemble the files to upload to the portal. It runs on your own computer, and your documents stay on it.

## 1. Start the program

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
| 1 | Company vault | Add your legal entities and upload certificates: turnover, completion certificates, PAN, GST, ISO, solvency. Type the values exactly as printed, then a reviewer clicks **Verify**. |
| 2 | Tenders, then New tender | Enter the name of work and tender ID, and upload **all** tender volumes and corrigenda. |
| 3 | Documents tab | Wait for processing. Every page is listed as processed, unreadable, duplicate and so on. Choose a type for any file marked "Choose type". |
| 4 | Clauses tab | Click each requirement to see the highlighted source page. Confirm it, correct the value, or reject it. |
| 5 | Command centre | Choose the bidder and enter the qualification cutoff date from the tender clause. |
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

## 4. Optional extras

- **Scanned tenders:** install Tesseract OCR (Windows installer from the UB Mannheim build) with English and Hindi. Without it, scanned pages are listed as "unreadable" rather than skipped silently.
- **Backups:** stop TenderPilot, then run `scripts\backup.ps1`. It writes a zip into the `backups` folder.

If something goes wrong, the screen tells you what happened and how to fix it. For technical details, see `README.md`.
