from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditEvent, Notification

_FORBIDDEN_KEYS = {"password", "text", "api_key", "secret", "token", "content"}


def audit(db: Session, *, org_id: int | None, user_id: int | None, action: str, subject_type: str,
          subject_id: int | None = None, tender_id: int | None = None, **details) -> None:
    """Record an audit event. Document text, passwords and secrets are stripped defensively."""
    clean = {k: v for k, v in details.items() if k.lower() not in _FORBIDDEN_KEYS}
    db.add(AuditEvent(org_id=org_id, user_id=user_id, action=action, subject_type=subject_type,
                      subject_id=subject_id, tender_id=tender_id, details=clean))


def notify(db: Session, *, org_id: int, message: str, tender_id: int | None = None, user_id: int | None = None) -> None:
    db.add(Notification(org_id=org_id, tender_id=tender_id, user_id=user_id, message=message))
