"""Request dependencies: current user, org boundary and permission checks."""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Membership, Tender, User
from .security import decode_token, has_permission


@dataclass
class Ctx:
    db: Session
    user: User
    org_id: int
    roles: set[str]

    def require(self, perm: str) -> None:
        if not has_permission(self.roles, perm):
            raise HTTPException(403, f"Permission '{perm}' required")

    def can(self, perm: str) -> bool:
        return has_permission(self.roles, perm)

    def tender(self, tender_id: int) -> Tender:
        t = self.db.get(Tender, tender_id)
        # Same 404 for "missing" and "other organization" so ids cannot be probed.
        if t is None or t.org_id != self.org_id:
            raise HTTPException(404, "Tender not found")
        return t

    def own(self, obj, label: str = "Record"):
        """Ensure an org-scoped object belongs to the caller's organization."""
        if obj is None or getattr(obj, "org_id", None) != self.org_id:
            raise HTTPException(404, f"{label} not found")
        return obj


def get_ctx(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Ctx:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated")
    try:
        payload = decode_token(authorization.split(" ", 1)[1])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired session")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.active:
        raise HTTPException(401, "User inactive")
    org_id = int(payload["org"])
    roles = set(db.scalars(select(Membership.role).where(Membership.user_id == user.id, Membership.org_id == org_id)))
    if not roles:
        raise HTTPException(403, "No membership in this organization")
    return Ctx(db=db, user=user, org_id=org_id, roles=roles)
