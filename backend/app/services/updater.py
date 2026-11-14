"""In-app software update for the installed Windows program.

Flow (an administrator drives each step from Settings & audit > Software update):
1. check()    asks the update feed (the project's latest GitHub Release) for its version, notes and installer.
2. download() fetches the installer in the background and verifies its SHA-256 against the checksum published
              with the release. Nothing is installed yet.
3. install()  writes a full data backup zip (database, stored files, secret key), starts the installer silently
              and stops this TenderPilot so the program files can be replaced. The installer starts TenderPilot
              again, which copies the database once more before applying any database update (migrations).

User data lives in %LOCALAPPDATA%\\TenderPilot, apart from the program folder, so installing never touches it.
Only the program installed by TenderPilot-Setup.exe can update itself; a developer copy (run from the source
folder) is updated with `git pull` instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..config import DATA_DIR
from ..version import APP_VERSION
from .backup import make_backup

FEED = os.environ.get("UPDATE_FEED", "https://api.github.com/repos/sodhi8279-gif/tenderpilot/releases/latest")
ALLOWED_HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_INSTALLER_BYTES = 800 * 1024 * 1024
UPDATES_DIR = DATA_DIR / "updates"
CHECK_EVERY_S = 12 * 3600


class UpdateError(RuntimeError):
    pass


_lock = threading.RLock()  # re-entrant: a nested status() call must never block
_state: dict = {"phase": "idle", "checked_at": None, "latest": None, "error": None,
                "downloaded": 0, "total": 0, "file": None, "backup": None}


def parse_version(v: str | None) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (v or "").lstrip("vV"))[:4]
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer(remote: str | None, local: str = APP_VERSION) -> bool:
    return parse_version(remote) > parse_version(local)


def demo_build() -> bool:
    return os.environ.get("DEMO_MODE") == "1" and "Demo" in Path(sys.executable).parent.name


def installer_name() -> str:
    return "TenderPilot-Demo-Setup.exe" if demo_build() else "TenderPilot-Setup.exe"


def can_install() -> tuple[bool, str]:
    """Only the packaged Windows program can replace itself."""
    if os.name != "nt":
        return False, "Updates from the app work in the installed Windows program only."
    if not getattr(sys, "frozen", False):
        return False, "This is the developer copy. Update it with 'git pull', then restart."
    return True, ""


def _get(url: str, timeout: int = 20):
    host = re.sub(r"^https://([^/]+)/.*$", r"\1", url)
    if not url.startswith("https://") or host not in ALLOWED_HOSTS:
        raise UpdateError(f"Update address not allowed: {host}")
    req = urllib.request.Request(url, headers={"User-Agent": f"TenderPilot/{APP_VERSION}",
                                               "Accept": "application/vnd.github+json, application/octet-stream"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310  (https and allow-listed hosts only)


def _fetch_release() -> dict:
    try:
        with _get(FEED) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except UpdateError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No version has been published on the update server yet.") from exc
        raise UpdateError(f"The update server answered with an error ({exc.code}). Try again later.") from exc
    except Exception as exc:  # noqa: BLE001
        raise UpdateError("Could not reach the update server. Check the internet connection and try again.") from exc
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
    name = installer_name()
    return {"version": str(rel.get("tag_name", "")).lstrip("vV"), "name": rel.get("name") or rel.get("tag_name"),
            "notes": (rel.get("body") or "").strip()[:8000], "published_at": rel.get("published_at"),
            "page": rel.get("html_url"), "installer_url": assets.get(name), "checksum_url": assets.get(f"{name}.sha256"),
            "installer_name": name}


def status() -> dict:
    ok, why = can_install()
    with _lock:
        s = dict(_state)
    latest = s["latest"]
    return {**s, "current_version": APP_VERSION, "update_available": bool(latest and is_newer(latest["version"])),
            "can_install": ok, "cannot_install_reason": why or None, "feed": FEED}


def check(force: bool = True) -> dict:
    with _lock:
        recent = _state["checked_at"] and time.time() - _state["checked_at"] < CHECK_EVERY_S
        skip = _state["phase"] in ("downloading", "installing") or (recent and not force)
    if skip:
        return status()  # (outside the lock: status() takes it itself)
    try:
        rel = _fetch_release()
        with _lock:
            _state.update(latest=rel, checked_at=time.time(), error=None)
            if _state["phase"] == "error":
                _state["phase"] = "idle"
    except UpdateError as exc:
        with _lock:
            _state.update(checked_at=time.time(), error=str(exc))
    return status()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_worker(rel: dict) -> None:
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    target = UPDATES_DIR / f"TenderPilot-{rel['version']}-{rel['installer_name']}"
    part = target.with_suffix(".part")
    try:
        with _get(rel["checksum_url"]) as r:
            expected = r.read().decode("ascii", "ignore").split()[0].strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise UpdateError("The published checksum is not valid; the update was not downloaded.")
        with _get(rel["installer_url"], timeout=60) as r, part.open("wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            if total > MAX_INSTALLER_BYTES:
                raise UpdateError("The installer is larger than expected; the update was not downloaded.")
            with _lock:
                _state.update(total=total, downloaded=0)
            done = 0
            for chunk in iter(lambda: r.read(1 << 20), b""):
                done += len(chunk)
                if done > MAX_INSTALLER_BYTES:
                    raise UpdateError("The installer is larger than expected; the update was not downloaded.")
                f.write(chunk)
                with _lock:
                    _state["downloaded"] = done
        if _sha256(part) != expected:
            raise UpdateError("The downloaded installer does not match its published checksum. It was deleted; "
                              "try again.")
        part.replace(target)
        with _lock:
            _state.update(phase="ready", file=str(target), error=None)
    except Exception as exc:  # noqa: BLE001
        part.unlink(missing_ok=True)
        with _lock:
            _state.update(phase="error", error=str(exc) if isinstance(exc, UpdateError)
                          else "The download failed. Check the internet connection and try again.")


def download() -> dict:
    with _lock:
        rel = _state["latest"]
        if _state["phase"] in ("downloading", "installing"):
            return status()
        if not rel or not is_newer(rel["version"]):
            raise UpdateError("No newer version to download. Check for updates first.")
        if not rel["installer_url"] or not rel["checksum_url"]:
            raise UpdateError("This release has no installer with a checksum for this program; it cannot be "
                              "installed from the app. Download it from the release page instead.")
        _state.update(phase="downloading", error=None, downloaded=0, total=0, file=None)
    threading.Thread(target=_download_worker, args=(rel,), daemon=True, name="tp-update-download").start()
    return status()


def _exit_soon(delay: float = 2.5) -> None:
    def bye():
        time.sleep(delay)
        os._exit(0)  # release the program files so the installer can replace them
    threading.Thread(target=bye, daemon=True).start()


def install(launch=subprocess.Popen, exit_after=_exit_soon) -> dict:
    """Back up all data, start the verified installer silently and stop this TenderPilot."""
    ok, why = can_install()
    if not ok:
        raise UpdateError(why)
    with _lock:
        if _state["phase"] != "ready" or not _state["file"] or not Path(_state["file"]).exists():
            raise UpdateError("Download the update first.")
        installer = Path(_state["file"])
        version = (_state["latest"] or {}).get("version")
        _state["phase"] = "installing"
    try:
        backup = make_backup(label=f"before-update-{version}")
    except Exception as exc:  # noqa: BLE001
        with _lock:
            _state.update(phase="ready", error=f"The backup could not be written, so nothing was installed: {exc}")
        raise UpdateError(_state["error"]) from exc
    log = UPDATES_DIR / f"install-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.log"
    args = [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", f"/LOG={log}"]
    if os.environ.get("DEMO_MODE") == "1" and not demo_build():
        args.append("/relaunchargs=--demo")
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    launch(args, creationflags=flags, close_fds=True)
    with _lock:
        _state["backup"] = str(backup)
    exit_after()
    return {**status(), "backup": str(backup), "message": "Installing. TenderPilot will close and start again by itself "
                                                          "in about a minute."}


def cleanup_old_installers() -> None:
    """Remove installers already installed (version not newer than this one). Called at start-up."""
    if not UPDATES_DIR.exists():
        return
    for f in UPDATES_DIR.glob("TenderPilot-*-*.exe"):
        m = re.match(r"TenderPilot-([\d.]+)-", f.name)
        if m and not is_newer(m.group(1)):
            f.unlink(missing_ok=True)
    for f in UPDATES_DIR.glob("*.part"):
        f.unlink(missing_ok=True)
