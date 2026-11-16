from __future__ import annotations

import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import settings
from ..db import get_db
from ..deps import Ctx, get_ctx
from ..models import ROLES, AuditEvent, Membership, Notification, Organization, User
from ..security import PERMISSIONS, create_token, hash_password, verify_password
from ..services import ai, convert, netconfig
from ..services.ingest import get_ocr
from .common import to_dict

router = APIRouter(prefix="/api/v1", tags=["auth"])


class SetupIn(BaseModel):
    org_name: str = Field(min_length=2, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=10, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str
    org_id: int | None = None


class UserIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=200)
    roles: list[str]


class OrgSettingsIn(BaseModel):
    allow_external_ai: bool | None = None
    retention_days: int | None = Field(default=None, ge=0, le=36500)


@router.get("/setup/status")
def setup_status(db: Session = Depends(get_db)):
    out: dict = {"needs_setup": db.scalar(select(func.count()).select_from(User)) == 0}
    # Demo build only (DEMO_MODE=1 is set by the demo launcher): show the shared SYNTHETIC demo sign-in.
    if os.environ.get("DEMO_MODE") == "1" and os.environ.get("DEMO_PASSWORD"):
        out["demo"] = {"email": "org.admin@demo.tenderpilot.local", "password": os.environ["DEMO_PASSWORD"]}
    return out


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")  # "testclient": the automated tests' local client


# ---- sign-in protection: too many wrong passwords block that e-mail (and that computer) for a while
FAIL_LIMIT, FAIL_WINDOW_S = 8, 15 * 60
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _recent_failures(key: str, now: float) -> int:
    with _fails_lock:
        times = [t for t in _fails.get(key, []) if now - t < FAIL_WINDOW_S]
        _fails[key] = times
        return len(times)


def _record_failure(*keys: str) -> None:
    now = time.time()
    with _fails_lock:
        for k in keys:
            _fails.setdefault(k, []).append(now)


@router.post("/setup")
def setup(body: SetupIn, request: Request, db: Session = Depends(get_db)):
    if netconfig.active()["network"] and not _is_local(request):
        raise HTTPException(403, "Create the organisation and the first administrator on the server itself "
                                 "(open TenderPilot on the server), then sign in here.")
    if db.scalar(select(func.count()).select_from(User)) > 0:
        raise HTTPException(409, "Already set up; ask an organization admin for an account")
    org = Organization(name=body.org_name)
    user = User(email=body.email.lower().strip(), name=body.name, password_hash=hash_password(body.password))
    db.add_all([org, user])
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id, role="org_admin"))
    audit(db, org_id=org.id, user_id=user.id, action="org.created", subject_type="organization", subject_id=org.id)
    db.commit()
    return {"token": create_token(user.id, org.id)}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.lower().strip()
    ip = request.client.host if request.client else "?"
    now = time.time()
    if _recent_failures(f"email:{email}", now) >= FAIL_LIMIT or _recent_failures(f"ip:{ip}", now) >= FAIL_LIMIT * 3:
        raise HTTPException(429, "Too many wrong passwords. Wait 15 minutes, or ask an administrator to reset the password.")
    user = db.scalar(select(User).where(User.email == email))
    if not user or not user.active or not verify_password(body.password, user.password_hash):
        _record_failure(f"email:{email}", f"ip:{ip}")
        raise HTTPException(401, "Incorrect email or password")
    with _fails_lock:
        _fails.pop(f"email:{email}", None)
    orgs = sorted(set(db.scalars(select(Membership.org_id).where(Membership.user_id == user.id))))
    if not orgs:
        raise HTTPException(403, "No organization membership")
    org_id = body.org_id if body.org_id in orgs else orgs[0]
    audit(db, org_id=org_id, user_id=user.id, action="auth.login", subject_type="user", subject_id=user.id)
    db.commit()
    return {"token": create_token(user.id, org_id), "org_id": org_id, "orgs": orgs}


@router.get("/me")
def me(ctx: Ctx = Depends(get_ctx)):
    org = ctx.db.get(Organization, ctx.org_id)
    perms = sorted(p for p, roles in PERMISSIONS.items() if roles & ctx.roles)
    unread = ctx.db.scalar(select(func.count()).select_from(Notification).where(
        Notification.org_id == ctx.org_id, Notification.read.is_(False)))
    return {"user": {"id": ctx.user.id, "name": ctx.user.name, "email": ctx.user.email},
            "org": {"id": org.id, "name": org.name, "allow_external_ai": org.allow_external_ai,
                    "retention_days": org.retention_days},
            "roles": sorted(ctx.roles), "permissions": perms, "unread_notifications": unread}


@router.get("/system")
def system(ctx: Ctx = Depends(get_ctx)):
    """Short summary (kept for compatibility). Full checks: GET /system/checks."""
    org = ctx.db.get(Organization, ctx.org_id)
    ocr = get_ocr()
    view = {"ai": ai.get_provider(ctx.db, org).describe(),
            "ai_configured": ai.configured_provider(ctx.db, org) is not None,
            "ocr": {"engine": ocr.name, "available": ocr.available(),
                    "note": None if ocr.available() else "Tesseract not found: scanned pages are reported as unreadable."},
            "database": settings.database_url.split(":", 1)[0], "queue": "database-backed worker",
            "converter": {"libreoffice": convert.available(),
                          "note": "LibreOffice available: legacy .doc/.xls are converted for reading (originals kept)."
                          if convert.available() else "LibreOffice not found: legacy .doc/.xls need manual conversion."}}
    ctx.db.commit()
    return view


@router.get("/users")
def list_users(ctx: Ctx = Depends(get_ctx)):
    ctx.require("tender.read")
    rows = ctx.db.execute(select(User, Membership.role).join(Membership, Membership.user_id == User.id)
                          .where(Membership.org_id == ctx.org_id)).all()
    users: dict[int, dict] = {}
    for u, role in rows:
        users.setdefault(u.id, {"id": u.id, "name": u.name, "email": u.email, "active": u.active, "roles": []})["roles"].append(role)
    return list(users.values())


@router.post("/users")
def create_user(body: UserIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    bad = [r for r in body.roles if r not in ROLES]
    if bad or not body.roles:
        raise HTTPException(422, f"Invalid roles {bad}; allowed {list(ROLES)}")
    email = body.email.lower().strip()
    user = ctx.db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=body.name, password_hash=hash_password(body.password))
        ctx.db.add(user)
        ctx.db.flush()
    for r in body.roles:
        exists = ctx.db.scalar(select(Membership).where(Membership.org_id == ctx.org_id, Membership.user_id == user.id, Membership.role == r))
        if not exists:
            ctx.db.add(Membership(org_id=ctx.org_id, user_id=user.id, role=r))
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="user.added", subject_type="user", subject_id=user.id, roles=body.roles)
    ctx.db.commit()
    return {"id": user.id}


class UserPatch(BaseModel):
    active: bool | None = None
    password: str | None = Field(default=None, min_length=10)
    name: str | None = Field(default=None, min_length=1, max_length=200)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, ctx: Ctx = Depends(get_ctx)):
    """Administrator: switch an account off or on, rename it, or set a new password (e.g. a forgotten one)."""
    ctx.require("org.admin")
    if not ctx.db.scalar(select(Membership).where(Membership.org_id == ctx.org_id, Membership.user_id == user_id)):
        raise HTTPException(404, "User not found")
    u = ctx.db.get(User, user_id)
    if body.active is False and u.id == ctx.user.id:
        raise HTTPException(409, "You cannot switch off your own account")
    changed = []
    if body.active is not None and body.active != u.active:
        u.active, _ = body.active, changed.append("active")
    if body.name:
        u.name, _ = body.name, changed.append("name")
    if body.password:
        u.password_hash, _ = hash_password(body.password), changed.append("password")
        with _fails_lock:
            _fails.pop(f"email:{u.email}", None)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="user.updated", subject_type="user", subject_id=u.id, changed=changed)
    ctx.db.commit()
    return {"id": u.id, "active": u.active, "name": u.name}


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


@router.post("/me/password")
def change_my_password(body: PasswordChangeIn, ctx: Ctx = Depends(get_ctx)):
    if not verify_password(body.current_password, ctx.user.password_hash):
        raise HTTPException(403, "The current password is not correct")
    ctx.user.password_hash = hash_password(body.new_password)
    audit(ctx.db, org_id=ctx.org_id, user_id=ctx.user.id, action="user.password_changed", subject_type="user", subject_id=ctx.user.id)
    ctx.db.commit()
    return {"ok": True}


@router.patch("/org")
def update_org(body: OrgSettingsIn, ctx: Ctx = Depends(get_ctx)):
    ctx.require("org.admin")
    org = ctx.db.get(Organization, ctx.org_id)
    if body.allow_external_ai is not None:
        if body.allow_external_ai:
            row = ai.settings_row(ctx.db, org.id)
            cfg = ai.load_config(ctx.db, org)
            if row.provider != "anthropic" or not cfg.key_source or row.last_test_ok is not True:
                raise HTTPException(409, "Before enabling: choose the Anthropic provider, save an API key and pass the "
                                         "connection test (Settings, AI provider).")
        org.allow_external_ai = body.allow_external_ai
    if body.retention_days is not None:
        org.retention_days = body.retention_days
    audit(ctx.db, org_id=org.id, user_id=ctx.user.id, action="org.settings", subject_type="organization", subject_id=org.id,
          allow_external_ai=org.allow_external_ai, retention_days=org.retention_days)
    ctx.db.commit()
    return to_dict(org)


@router.get("/notifications")
def notifications(ctx: Ctx = Depends(get_ctx)):
    rows = ctx.db.scalars(select(Notification).where(Notification.org_id == ctx.org_id)
                          .order_by(Notification.id.desc()).limit(100))
    return [to_dict(n) for n in rows]


@router.post("/notifications/read")
def mark_read(ctx: Ctx = Depends(get_ctx)):
    for n in ctx.db.scalars(select(Notification).where(Notification.org_id == ctx.org_id, Notification.read.is_(False))):
        n.read = True
    ctx.db.commit()
    return {"ok": True}


@router.get("/audit")
def audit_log(tender_id: int | None = None, limit: int = 200, ctx: Ctx = Depends(get_ctx)):
    ctx.require("audit.read")
    stmt = select(AuditEvent).where(AuditEvent.org_id == ctx.org_id)
    if tender_id:
        stmt = stmt.where(AuditEvent.tender_id == tender_id)
    users = {u.id: u.name for u in ctx.db.scalars(select(User))}
    return [{**to_dict(e), "user_name": users.get(e.user_id, "system")}
            for e in ctx.db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(min(limit, 1000)))]
