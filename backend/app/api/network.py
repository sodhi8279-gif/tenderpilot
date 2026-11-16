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
            "can_restart": _can_restart(), "demo": demo}


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


@router.post("/restart")
def restart(ctx: Ctx = Depends(get_ctx)):
    """Installed Windows program only: start TenderPilot again so new network settings apply."""
    ctx.require("org.admin")
    if not _can_restart():
        raise HTTPException(409, "Restart TenderPilot yourself: close its window and start it again.")
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="network.restart", subject_type="software")
    ctx.db.commit()
    exe = sys.executable
    # A helper waits until this TenderPilot has stopped (port free), then starts it again without opening a browser.
    cmd = f'ping -n 6 127.0.0.1 >nul & start "" "{exe}" --no-browser'
    subprocess.Popen(["cmd", "/c", cmd], creationflags=0x00000008 | 0x00000200, close_fds=True)

    def bye():
        time.sleep(1.5)
        os._exit(0)
    threading.Thread(target=bye, daemon=True).start()
    return {"message": "TenderPilot is restarting with the new settings. Reload this page in about 20 seconds."}
