"""In-app update: check, verified download, backup before install, admin only. Uses a fake update server."""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

import pytest

from app.services import updater
from app.version import APP_VERSION

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = b"MZ" + b"fake installer " * 1000


class _Resp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _feed(monkeypatch, version="99.0.0", checksum=None, with_installer=True):
    rel = {"tag_name": f"v{version}", "name": f"TenderPilot {version}", "body": "## What's new\n- Faster reading",
           "html_url": "https://github.com/x/y/releases/tag/v" + version, "published_at": "2026-11-20T10:00:00Z",
           "assets": ([{"name": "TenderPilot-Setup.exe", "browser_download_url": "https://github.com/x/y/TenderPilot-Setup.exe"},
                       {"name": "TenderPilot-Setup.exe.sha256", "browser_download_url": "https://github.com/x/y/TenderPilot-Setup.exe.sha256"}]
                      if with_installer else [])}
    digest = checksum or hashlib.sha256(INSTALLER).hexdigest()

    def fake_get(url, timeout=20):
        if url == updater.FEED:
            return _Resp(json.dumps(rel).encode())
        if url.endswith(".sha256"):
            return _Resp(f"{digest}  TenderPilot-Setup.exe\n".encode())
        if url.endswith(".exe"):
            return _Resp(INSTALLER)
        raise AssertionError(url)
    monkeypatch.setattr(updater, "_get", fake_get)
    monkeypatch.setattr(updater, "UPDATES_DIR", Path(__import__("tempfile").mkdtemp()))
    updater._state.update(phase="idle", latest=None, checked_at=None, error=None, file=None, backup=None)


def _wait():
    for _ in range(100):
        if updater._state["phase"] != "downloading":
            return
        time.sleep(0.05)


def test_versions_agree_everywhere():
    iss = (ROOT / "packaging" / "tenderpilot.iss").read_text(encoding="utf-8")
    assert re.search(r'#define AppVersion "([\d.]+)"', iss).group(1) == APP_VERSION
    info = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    assert f"'ProductVersion', '{APP_VERSION}'" in info
    assert f"## {APP_VERSION}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_version_comparison():
    assert updater.is_newer("1.10.0", "1.9.3") and updater.is_newer("v2.0", "1.9.9")
    assert not updater.is_newer("1.2.0", "1.2.0") and not updater.is_newer("", "1.0.0")


def test_check_and_verified_download(monkeypatch):
    _feed(monkeypatch)
    s = updater.check()
    assert s["update_available"] and s["latest"]["version"] == "99.0.0" and "Faster reading" in s["latest"]["notes"]
    updater.download()
    _wait()
    s = updater.status()
    assert s["phase"] == "ready" and Path(s["file"]).read_bytes() == INSTALLER


def test_tampered_download_is_rejected_and_deleted(monkeypatch):
    _feed(monkeypatch, checksum="0" * 64)
    updater.check()
    updater.download()
    _wait()
    s = updater.status()
    assert s["phase"] == "error" and "does not match" in s["error"] and s["file"] is None
    assert not list(updater.UPDATES_DIR.glob("*.exe")) and not list(updater.UPDATES_DIR.glob("*.part"))


def test_no_newer_version_or_no_installer(monkeypatch):
    _feed(monkeypatch, version=APP_VERSION)
    assert updater.check()["update_available"] is False
    with pytest.raises(updater.UpdateError):
        updater.download()
    _feed(monkeypatch, with_installer=False)
    updater.check()
    with pytest.raises(updater.UpdateError, match="no installer"):
        updater.download()


def test_only_allowed_https_hosts():
    with pytest.raises(updater.UpdateError):
        updater._get("http://github.com/x")
    with pytest.raises(updater.UpdateError):
        updater._get("https://evil.example.com/TenderPilot-Setup.exe")


def test_developer_copy_cannot_install():
    ok, why = updater.can_install()
    assert not ok and why


def test_install_backs_up_everything_first(org, monkeypatch):
    from helpers import tender
    tender(org)  # some data
    _feed(monkeypatch)
    updater.check()
    updater.download()
    _wait()
    monkeypatch.setattr(updater, "can_install", lambda: (True, ""))
    launched, exited = [], []
    out = updater.install(launch=lambda args, **kw: launched.append(args), exit_after=lambda: exited.append(1))
    backup = Path(out["backup"])
    assert backup.exists() and "before-update-99.0.0" in backup.name
    with zipfile.ZipFile(backup) as z:
        assert "tenderpilot.db" in z.namelist()
    assert launched and launched[0][0] == updater._state["file"] and "/SILENT" in launched[0] and exited == [1]


def test_install_stops_if_backup_fails(monkeypatch):
    _feed(monkeypatch)
    updater.check()
    updater.download()
    _wait()
    monkeypatch.setattr(updater, "can_install", lambda: (True, ""))
    monkeypatch.setattr(updater, "make_backup", lambda **kw: (_ for _ in ()).throw(OSError("disk full")))
    launched = []
    with pytest.raises(updater.UpdateError, match="nothing was installed"):
        updater.install(launch=lambda args, **kw: launched.append(args), exit_after=lambda: None)
    assert launched == [] and updater.status()["phase"] == "ready"


def test_update_screens_are_for_administrators(org, monkeypatch):
    _feed(monkeypatch)
    assert org.get("/api/v1/update")["current_version"] == APP_VERSION
    org.req("GET", "/api/v1/update", role="tender_manager", expect=403)
    org.post("/api/v1/update/check", role="estimator", expect=403)
    assert org.post("/api/v1/update/check").json()["update_available"] is True
    org.post("/api/v1/update/install", json={"confirm": True}, expect=409)  # developer copy: refused, nothing happens


def test_old_installers_are_tidied(monkeypatch):
    _feed(monkeypatch)
    old = updater.UPDATES_DIR / f"TenderPilot-{APP_VERSION}-TenderPilot-Setup.exe"
    new = updater.UPDATES_DIR / "TenderPilot-99.0.0-TenderPilot-Setup.exe"
    for f in (old, new):
        f.write_bytes(b"x")
    updater.cleanup_old_installers()
    assert not old.exists() and new.exists()


def test_no_release_yet_is_explained(monkeypatch):
    import urllib.error
    _feed(monkeypatch)

    def not_found(url, timeout=20):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(updater, "_get", not_found)
    s = updater.check()
    assert s["update_available"] is False and "No version has been published" in s["error"]


def test_quiet_check_after_a_recent_check_returns_at_once(monkeypatch):
    """Regression: a second check within 12 hours used to wait forever on its own lock."""
    import threading
    _feed(monkeypatch)
    updater.check()
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("s", updater.check(force=False)), daemon=True)
    t.start()
    t.join(3)
    assert not t.is_alive() and out["s"]["update_available"] is True


def test_update_check_trusts_the_shipped_certificate_list():
    import certifi
    import ssl as _ssl
    ctx = updater.ssl_context()
    assert ctx.verify_mode == _ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] >= 100  # certifi's roots are loaded
    assert certifi.where()


def test_network_errors_are_explained():
    import socket as _socket
    import ssl as _ssl
    import urllib.error
    cert = urllib.error.URLError(_ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer"))
    assert "certificate problem" in updater.explain(cert)
    assert "no internet" in updater.explain(urllib.error.URLError(_socket.gaierror(11001, "getaddrinfo failed")))
    assert "did not answer in time" in updater.explain(urllib.error.URLError(TimeoutError("timed out")))
    assert "refused" in updater.explain(urllib.error.URLError(ConnectionRefusedError(10061, "refused")))


def test_page_is_never_cached_so_updates_show_at_once(client):
    from app import main
    if not main.DIST.exists():
        pytest.skip("frontend not built")
    r = client.get("/settings")
    assert "no-store" in r.headers.get("cache-control", "")


# ------------------------------------------------------------------ setup file uploaded by an admin
def _vs(key: str, val: str) -> bytes:
    """A version-resource string entry as Windows stores it (UTF-16, value 32-bit aligned)."""
    k = (key + "\0").encode("utf-16-le")
    pad = b"\0" * ((4 - (6 + len(k)) % 4) % 4)
    return b"\0" * 6 + k + pad + (val + "\0").encode("utf-16-le")


def fake_setup(version="99.0.0", product="TenderPilot", desc="TenderPilot Setup") -> bytes:
    body = b"MZ" + b"\0" * 64 + b"inno setup stub " * 2000
    return body + _vs("CompanyName", "TenderPilot") + _vs("FileDescription", desc) + _vs("ProductName", product) + _vs("ProductVersion", version)


def _upload_env(monkeypatch, published=None):
    monkeypatch.setattr(updater, "UPDATES_DIR", Path(__import__("tempfile").mkdtemp()))
    monkeypatch.setattr(updater, "can_install", lambda: (True, ""))
    monkeypatch.setattr(updater, "_published_checksum", lambda v, n: published)
    updater._state.update(phase="idle", latest=None, checked_at=None, error=None, file=None, backup=None)


def _send(org, data: bytes, name="TenderPilot-Setup.exe", password="not-used-in-tests", **kw):
    return org.req("POST", "/api/v1/update/upload", data={"password": password},
                   files={"file": (name, data, "application/octet-stream")}, **kw)


def test_version_info_is_read_from_the_setup_program():
    info = updater.exe_version_info(fake_setup("1.6.0"))
    assert info["ProductName"] == "TenderPilot" and info["ProductVersion"] == "1.6.0"


def test_admin_uploads_a_setup_file_then_install_runs_it(org, monkeypatch):
    data = fake_setup()
    _upload_env(monkeypatch, published=hashlib.sha256(data).hexdigest())
    out = _send(org, data).json()
    assert out["phase"] == "ready" and out["latest"]["version"] == "99.0.0" and out["latest"]["source"] == "upload"
    assert out["latest"]["verified_published"] and "published" in out["latest"]["check"]
    assert Path(out["file"]).read_bytes() == data and not list(updater.UPDATES_DIR.glob("*.part"))
    updater.check()  # a background check must not swap in another version behind the uploaded file
    assert updater._state["latest"]["source"] == "upload"
    launched = []
    res = updater.install(launch=lambda args, **kw: launched.append(args), exit_after=lambda: None)
    assert launched[0][0] == out["file"] and "before-update-99.0.0" in res["backup"]


def test_upload_from_the_release_zip_checks_the_checksum_inside(org, monkeypatch):
    data = fake_setup()
    _upload_env(monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("TenderPilot-Setup.exe", data)
        z.writestr("TenderPilot-Setup.exe.sha256", hashlib.sha256(data).hexdigest() + "  TenderPilot-Setup.exe\n")
        z.writestr("INSTALL-ON-SERVER.txt", "guide")
    out = _send(org, buf.getvalue(), name="TenderPilot-99.0.0-Windows-Setup.zip").json()
    assert out["phase"] == "ready" and "zip" in out["latest"]["check"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("TenderPilot-Setup.exe", data + b"x")
        z.writestr("TenderPilot-Setup.exe.sha256", hashlib.sha256(data).hexdigest())
    _upload_env(monkeypatch)
    assert "damaged" in _send(org, buf.getvalue(), name="t.zip", expect=409).json()["detail"]


@pytest.mark.parametrize("data,expected", [
    (b"%PDF-1.7 not a program", "not a Windows setup program"),
    (fake_setup(product="Some Other App"), "not the TenderPilot installer"),
    (fake_setup(version=APP_VERSION), "already installed"),
    (fake_setup(version="0.9.0"), "already installed"),
])
def test_wrong_files_are_refused_and_deleted(org, monkeypatch, data, expected):
    _upload_env(monkeypatch)
    r = _send(org, data, expect=409).json()
    assert expected in r["detail"]
    assert not list(updater.UPDATES_DIR.iterdir()) and updater._state["phase"] == "idle"


def test_file_that_differs_from_the_published_one_is_refused(org, monkeypatch):
    _upload_env(monkeypatch, published="0" * 64)
    assert "not the one published" in _send(org, fake_setup(), expect=409).json()["detail"]
    assert not list(updater.UPDATES_DIR.iterdir())


def test_offline_server_accepts_with_a_warning(org, monkeypatch):
    _upload_env(monkeypatch, published=None)
    out = _send(org, fake_setup()).json()
    assert not out["latest"]["verified_published"] and "no internet" in out["latest"]["check"]


def test_only_an_admin_with_the_right_password_can_upload(org, monkeypatch):
    _upload_env(monkeypatch)
    assert "password" in _send(org, fake_setup(), password="wrong", expect=403).json()["detail"]
    _send(org, fake_setup(), role="tender_manager", expect=403)
    assert updater._state["phase"] == "idle" and not list(updater.UPDATES_DIR.iterdir())
