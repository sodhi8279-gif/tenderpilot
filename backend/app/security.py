"""Authentication (bcrypt + JWT) and server-side role permissions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings

ALGO = "HS256"

# permission -> roles allowed. Enforced on the server for every endpoint.
PERMISSIONS: dict[str, set[str]] = {
    "org.admin": {"org_admin"},
    "tender.read": {"org_admin", "tender_manager", "technical_reviewer", "estimator", "document_coordinator",
                    "finance_reviewer", "authorized_approver", "auditor"},
    "tender.write": {"org_admin", "tender_manager"},
    "documents.write": {"org_admin", "tender_manager", "document_coordinator", "technical_reviewer"},
    "requirements.review": {"org_admin", "tender_manager", "technical_reviewer"},
    "rules.approve": {"org_admin", "tender_manager", "technical_reviewer", "authorized_approver"},
    "eligibility.override": {"org_admin", "authorized_approver"},
    "vault.read": {"org_admin", "tender_manager", "technical_reviewer", "document_coordinator", "finance_reviewer",
                   "authorized_approver", "auditor"},
    "vault.write": {"org_admin", "tender_manager", "document_coordinator"},
    "vault.verify": {"org_admin", "tender_manager", "authorized_approver"},
    "tasks.write": {"org_admin", "tender_manager", "document_coordinator", "technical_reviewer", "estimator"},
    "docs.generate": {"org_admin", "tender_manager", "document_coordinator", "technical_reviewer"},
    "technical.write": {"org_admin", "tender_manager", "technical_reviewer"},
    # price data is restricted: coordinators/technical reviewers cannot read prices
    "price.read": {"org_admin", "tender_manager", "estimator", "finance_reviewer", "authorized_approver", "auditor"},
    "price.write": {"org_admin", "estimator"},
    "quote.estimator_review": {"estimator", "org_admin"},
    "quote.approve": {"authorized_approver", "finance_reviewer"},
    "package.build": {"org_admin", "tender_manager", "document_coordinator"},
    "package.approve": {"authorized_approver"},
    "package.download": {"org_admin", "tender_manager", "authorized_approver", "document_coordinator"},
    "receipt.record": {"org_admin", "tender_manager", "document_coordinator"},
    "audit.read": {"org_admin", "auditor", "tender_manager"},
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int, org_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "org": org_id, "iat": now, "exp": now + timedelta(hours=settings.token_hours)}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGO)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[ALGO])


def has_permission(roles: set[str], perm: str) -> bool:
    return bool(roles & PERMISSIONS.get(perm, set()))
