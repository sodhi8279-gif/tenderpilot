"""Software update from the app (administrators only). See services/updater.py for the flow and safeguards."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..services import updater

router = APIRouter(prefix="/api/v1/update", tags=["update"])


@router.get("")
def update_status(ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    return updater.status()


@router.post("/check")
def update_check(force: bool = True, ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    return updater.check(force=force)


@router.post("/download")
def update_download(ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    try:
        out = updater.download()
    except updater.UpdateError as exc:
        raise HTTPException(409, str(exc))
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="update.download", subject_type="software",
          version=(out.get("latest") or {}).get("version"))
    ctx.db.commit()
    return out


class InstallIn(BaseModel):
    confirm: bool = False


@router.post("/install")
def update_install(body: InstallIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    if not body.confirm:
        raise HTTPException(422, "Confirm the installation")
    version = (updater.status().get("latest") or {}).get("version")
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="update.install", subject_type="software", version=version)
    ctx.db.commit()  # recorded before TenderPilot stops for the installer
    try:
        return updater.install()
    except updater.UpdateError as exc:
        raise HTTPException(409, str(exc))
