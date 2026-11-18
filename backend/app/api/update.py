"""Software update from the app (administrators only). See services/updater.py for the flow and safeguards."""
from __future__ import annotations

import secrets
import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ..audit import audit
from ..deps import Ctx, get_ctx
from ..security import verify_password
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


@router.post("/upload")
async def update_upload(file: UploadFile = File(...), password: str = Form(...), ctx: Ctx = Depends(get_ctx)):
    """An admin uploads TenderPilot-Setup.exe (or the release zip) from their own computer; "Install now" runs it."""
    ctx.require("org.admin")
    if not verify_password(password, ctx.user.password_hash):
        raise HTTPException(403, "The password is not right. Type your own TenderPilot password to install software.")
    ok, why = updater.can_install()
    if not ok:
        raise HTTPException(409, why)
    updater.UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    part = updater.UPDATES_DIR / f"upload-{secrets.token_hex(6)}.part"
    size = 0
    try:
        with part.open("wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > updater.MAX_INSTALLER_BYTES:
                    raise HTTPException(413, "This file is far larger than a TenderPilot installer.")
                f.write(chunk)
        name = file.filename or "upload"
        zip_sum = None
        if name.lower().endswith(".zip"):
            part, zip_sum = await run_in_threadpool(_from_zip, part)
        out = await run_in_threadpool(updater.accept_uploaded, part, name, zip_sum)
    except updater.UpdateError as exc:
        part.unlink(missing_ok=True)
        audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="update.upload_refused", subject_type="software",
              file=file.filename, reason=str(exc))
        ctx.db.commit()
        raise HTTPException(409, str(exc))
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    latest = out.get("latest") or {}
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="update.upload", subject_type="software",
          file=file.filename, version=latest.get("version"), sha256=latest.get("sha256"), check=latest.get("check"))
    ctx.db.commit()
    return out


def _from_zip(part):
    """TenderPilot-x.y.z-Windows-Setup.zip: take the installer (and its .sha256) out of it."""
    import zipfile
    want = updater.installer_name()
    try:
        with zipfile.ZipFile(part) as z:
            names = {n.rsplit("/", 1)[-1]: n for n in z.namelist()}
            if want not in names:
                raise updater.UpdateError(f"The zip has no {want} in it.")
            info = z.getinfo(names[want])
            if info.file_size > updater.MAX_INSTALLER_BYTES:
                raise updater.UpdateError("The installer in the zip is far larger than expected.")
            exe = part.with_suffix(".exe.part")
            try:
                with z.open(info) as src, exe.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
            except Exception:
                exe.unlink(missing_ok=True)
                raise
            checksum = None
            if f"{want}.sha256" in names:
                got = z.read(names[f"{want}.sha256"]).decode("ascii", "ignore").split()
                checksum = got[0].lower() if got and len(got[0]) == 64 else None
    except zipfile.BadZipFile:
        raise updater.UpdateError("This zip file is damaged. Download it again.")
    finally:
        part.unlink(missing_ok=True)
    return exe, checksum


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
