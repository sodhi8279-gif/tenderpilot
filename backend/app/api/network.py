"""Network access settings (administrators) and the server certificate for staff computers."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..services import netconfig

router = APIRouter(prefix="/api/v1/network", tags=["network"])


def _can_restart() -> bool:
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


@router.get("")
def network_status(ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    cfg, act = netconfig.read_config(), netconfig.active()
    demo = os.environ.get("DEMO_MODE") == "1"
    wanted = (cfg["network"], cfg["port"], cfg["network"] and cfg["https"])
    running = (act["network"], act["port"], act["network"] and act["https"])
    return {"config": cfg, "active": act, "restart_needed": not demo and wanted != running,
            "addresses": netconfig.addresses(), "urls": netconfig.urls(cfg["port"], cfg["https"]),
            "firewall_command": netconfig.firewall_command(cfg["port"]), "certificate": netconfig.cert_info(),
            "can_restart": _can_restart(), "demo": demo, "autostart": autostart_state()}


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_state() -> bool | None:
    """Start with Windows (same entry the installer's "Start TenderPilot when I sign in" writes). None: not offered here."""
    if not _can_restart() or os.environ.get("DEMO_MODE") == "1":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "TenderPilot")
            return True
    except OSError:
        return False


class AutostartIn(BaseModel):
    on: bool


@router.put("/autostart")
def set_autostart(body: AutostartIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    if autostart_state() is None:
        raise HTTPException(409, "Starting with Windows can be set in the installed Windows program only.")
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if body.on:
            winreg.SetValueEx(k, "TenderPilot", 0, winreg.REG_SZ, f'"{sys.executable}" --no-browser')
        else:
            try:
                winreg.DeleteValue(k, "TenderPilot")
            except OSError:
                pass
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="network.autostart", subject_type="software", on=body.on)
    ctx.db.commit()
    return {"autostart": autostart_state()}


class NetworkIn(BaseModel):
    network: bool
    port: int = Field(default=netconfig.DEFAULT_PORT, ge=1024, le=65535)
    https: bool = True


@router.put("")
def network_save(body: NetworkIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    if os.environ.get("DEMO_MODE") == "1" and body.network:
        raise HTTPException(409, "The demo cannot be opened to the network: it shows its sign-in on the login page.")
    netconfig.write_config(body.network, body.port, body.https)
    if body.network and body.https:
        netconfig.ensure_cert()
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="network.settings", subject_type="software",
          network=body.network, port=body.port, https=body.https)
    ctx.db.commit()
    return network_status(ctx)


@router.post("/certificate/renew")
def renew_certificate(ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    netconfig.ensure_cert(force=True)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="network.certificate_renewed", subject_type="software")
    ctx.db.commit()
    return {"certificate": netconfig.cert_info(), "restart_needed": netconfig.active()["https"]}


@router.get("/certificate")
def download_certificate(ctx: Ctx = Depends(get_ctx)):
    """Any signed-in user: the server certificate, to install on a staff computer so the browser trusts it."""
    if not netconfig.CERT.exists():
        raise HTTPException(404, "No certificate yet: turn on network access with HTTPS first")
    return Response(netconfig.CERT.read_bytes(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="TenderPilot-server.crt"'})


def restart_command() -> list[str]:
    return [sys.executable, "--no-browser", "--after-restart", str(os.getpid())]


@router.post("/restart")
def restart(ctx: Ctx = Depends(get_ctx)):
    """Installed Windows program only: start TenderPilot again so new network settings apply."""
    ctx.require("org.admin")
    if not _can_restart():
        raise HTTPException(409, "Restart TenderPilot yourself: close its window and start it again.")
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="network.restart", subject_type="software")
    ctx.db.commit()
    # The new TenderPilot gets its own window and waits until this one has stopped. No command shell: an
    # argument list cannot be broken by quoting (1.4.0 showed "Windows cannot find '\\'" here).
    subprocess.Popen(restart_command(), creationflags=0x00000010 | 0x00000200, close_fds=True)  # NEW_CONSOLE | NEW_GROUP

    def bye():
        time.sleep(1.5)
        os._exit(0)
    threading.Thread(target=bye, daemon=True).start()
    return {"message": "TenderPilot is restarting with the new settings. Reload this page in about 20 seconds."}
