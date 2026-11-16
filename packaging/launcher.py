"""TenderPilot desktop launcher (the program inside TenderPilot.exe).

  TenderPilot.exe            start TenderPilot and open it in the browser
  TenderPilot.exe --demo     start the demo version (separate SYNTHETIC data, created on first run)
  TenderPilot.exe --backup   write a consistent backup zip of this computer's TenderPilot data
  TenderPilot.exe --local    start for this computer only, even if network access is turned on

Program files and user data are kept apart: data lives in %LOCALAPPDATA%\\TenderPilot (or
TenderPilot-Demo), so reinstalling or uninstalling the program never deletes tenders or documents.
By default only 127.0.0.1 is used, so nothing is reachable from other computers. When an administrator turns on
network access (Settings > Network access; stored in data\\server.json), TenderPilot listens on the server's network
addresses over HTTPS so staff can use it from their own browsers. The demo is never opened to the network.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

DEMO_PASSWORD = "DemoPass-2026!"  # shared sign-in for the SYNTHETIC demo only; shown on its sign-in page
PORTS = {False: 8471, True: 8472}


def bundle_dir() -> Path:
    """Folder holding the bundled program (PyInstaller) or the source checkout when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def home_dir(demo: bool) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / ("TenderPilot-Demo" if demo else "TenderPilot")


def configure_environment(demo: bool) -> Path:
    """Point the app at per-user data and at the bundled web interface, OCR engine and language data.
    Must run before anything imports the app package (settings are read at import)."""
    home = home_dir(demo)
    (home / "data").mkdir(parents=True, exist_ok=True)
    b = bundle_dir()
    frozen = getattr(sys, "frozen", False)
    os.environ.setdefault("DATA_DIR", str(home / "data"))
    os.environ.setdefault("BACKUP_DIR", str(home / "backups"))
    os.environ.setdefault("FRONTEND_DIST", str(b / "frontend" / "dist"))
    tess = b / "tesseract"
    if frozen and (tess / "tesseract.exe").exists():
        os.environ.setdefault("TESSERACT_CMD", str(tess / "tesseract.exe"))
        os.environ.setdefault("TESSDATA_DIR", str(tess / "tessdata"))
    elif not frozen and (b / "backend" / "data" / "tessdata").is_dir():
        os.environ.setdefault("TESSDATA_DIR", str(b / "backend" / "data" / "tessdata"))
    os.environ.setdefault("CORS_ORIGINS", "http://127.0.0.1")
    if demo:
        os.environ["DEMO_MODE"] = "1"
        os.environ["DEMO_PASSWORD"] = DEMO_PASSWORD
    if not frozen:
        sys.path.insert(0, str(b / "backend"))
    return home


def probe(port: int) -> dict | None:
    """Is a TenderPilot answering on this port (over http or https)?"""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # our own self-signed certificate
    for scheme in ("http", "https"):
        try:
            with urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/api/health", timeout=1.5,
                                        context=ctx if scheme == "https" else None) as r:
                return json.loads(r.read().decode())
        except Exception:
            continue
    return None


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(demo: bool, wanted: int, network: bool) -> tuple[int, bool]:
    """Return (port, already_running). Reuses a TenderPilot of the same kind if one is running."""
    port = wanted
    info = probe(port)
    if info and info.get("app") == "TenderPilot" and bool(info.get("demo")) == demo:
        return port, True
    if port_free(port, "0.0.0.0" if network else "127.0.0.1"):
        return port, False
    if network:  # staff use a fixed address: never move silently
        raise RuntimeError(f"Port {port} is used by another program. Choose another port in Settings > Network access "
                           f"(start once with --local to reach Settings), or stop the other program.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:  # someone else has our port: take any free one
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1], False


def open_when_ready(port: int, browser: bool, https: bool = False, staff_urls: list[str] | None = None) -> None:
    url = f"{'https' if https else 'http'}://127.0.0.1:{port}"
    for _ in range(240):
        if probe(port):
            if browser:
                webbrowser.open(url)
            print(f"\nTenderPilot is running at {url}")
            if staff_urls:
                print("Network access is ON. Staff open one of these addresses in their browser:")
                for u in staff_urls:
                    print(f"   {u}")
                if https:
                    print("The first time, the browser warns about the certificate: see Settings > Network access.")
            print("Keep this window open while you work. Close it to stop TenderPilot.\n")
            return
        time.sleep(0.5)
    print("TenderPilot did not start within 2 minutes. Close this window and try again.")


def seed_demo_if_needed() -> None:
    from sqlalchemy import select

    from app.config import settings
    from app.db import SessionLocal
    from app.migrations import migrate
    from app.models import Organization
    import seed_demo

    migrate()  # the tables must exist before we can ask whether the demo was already created
    with SessionLocal() as db:
        if db.scalar(select(Organization).where(Organization.name == seed_demo.ORG_NAME)):
            return
    print("First start of the demo: creating SYNTHETIC sample data (about a minute)...")
    worker_setting = settings.run_worker_in_process
    settings.run_worker_in_process = False  # the seeder processes its own jobs; avoid a second worker racing it
    try:
        seed_demo.main()
    finally:
        settings.run_worker_in_process = worker_setting


def backup() -> int:
    from app.config import DATA_DIR
    from app.services.backup import BackupError, make_backup

    try:
        out = make_backup()
    except BackupError as exc:
        print(exc)
        return 1
    print(f"Backup written to {out}")
    print(f"Copy it to another drive. To restore: close TenderPilot, then unzip it into {DATA_DIR}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="TenderPilot")
    ap.add_argument("--demo", action="store_true", help="run the demo version with SYNTHETIC data")
    ap.add_argument("--backup", action="store_true", help="back up this computer's TenderPilot data and exit")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--local", action="store_true", help="this computer only, even if network access is turned on")
    args = ap.parse_args()

    home = configure_environment(args.demo)
    title = "TenderPilot DEMO" if args.demo else "TenderPilot"
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(f"{title} - keep this window open")
    print(f"{title}\nData folder: {home}")

    if args.backup:
        code = backup()
        time.sleep(4)
        return code

    from app.services import netconfig  # reads data\server.json (environment is set up above)

    cfg = netconfig.read_config()
    network = cfg["network"] and not args.demo and not args.local
    https = network and cfg["https"]
    wanted = PORTS[True] if args.demo else cfg["port"]
    port, running = pick_port(args.demo, wanted, network)
    if running:
        print("TenderPilot is already running; opening it in your browser.")
        if not args.no_browser:
            info = probe(port) or {}
            webbrowser.open(f"{'https' if info.get('https') else 'http'}://127.0.0.1:{port}")
        time.sleep(2)
        return 0
    os.environ["TP_NETWORK_ACTIVE"] = "1" if network else "0"
    os.environ["TP_HTTPS_ACTIVE"] = "1" if https else "0"
    os.environ["TP_PORT"] = str(port)
    ssl_args: dict = {}
    if https:
        netconfig.ensure_cert()
        ssl_args = {"ssl_certfile": str(netconfig.CERT), "ssl_keyfile": str(netconfig.KEY)}

    import uvicorn

    from app.main import app  # settings are read here; migrations run at startup

    if args.demo:
        seed_demo_if_needed()
        print(f"Demo sign-in: org.admin@demo.tenderpilot.local / {DEMO_PASSWORD}")
    staff = netconfig.urls(port, https) if network else None
    threading.Thread(target=open_when_ready, args=(port, not args.no_browser, https, staff), daemon=True).start()
    print("Starting..." + (" (network access ON)" if network else ""))
    uvicorn.run(app, host="0.0.0.0" if network else "127.0.0.1", port=port, log_level="warning", loop="asyncio",
                http="h11", lifespan="on", **ssl_args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # keep the window open so the person can read what went wrong
        print(f"\nTenderPilot could not start: {type(exc).__name__}: {exc}")
        print("Take a photo of this message for support, then press Enter to close.")
        try:
            input()
        except EOFError:
            pass
        sys.exit(1)
