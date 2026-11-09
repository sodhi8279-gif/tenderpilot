"""Test harness: isolated temp database + storage, in-process API client, synthetic fixtures."""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="tenderpilot-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["STORAGE_DIR"] = str(_TMP / "storage")
os.environ["RUN_WORKER"] = "0"  # tests drain the queue explicitly
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-0123456789"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.services.ingest as _ingest  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Membership, Organization, User  # noqa: E402
from app.security import create_token, hash_password  # noqa: E402
from app.services import jobs  # noqa: E402

# Force "no OCR engine" so results do not depend on whether Tesseract is installed on the test machine.
_ingest.get_ocr = lambda: _ingest.OcrAdapter()  # type: ignore[assignment]

ALL_ROLES = ["org_admin", "tender_manager", "technical_reviewer", "estimator", "document_coordinator",
             "finance_reviewer", "authorized_approver", "auditor"]


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


class Org:
    def __init__(self, client: TestClient, name: str):
        self.client = client
        self.tokens: dict[str, str] = {}
        self.users: dict[str, int] = {}
        with SessionLocal() as db:
            org = Organization(name=name)
            db.add(org)
            db.flush()
            self.id = org.id
            for role in ALL_ROLES + ["approver2"]:
                u = User(email=f"{role}-{uuid.uuid4().hex[:8]}@example.test", name=f"{role} user",
                         password_hash=hash_password("not-used-in-tests"))
                db.add(u)
                db.flush()
                db.add(Membership(org_id=org.id, user_id=u.id, role="authorized_approver" if role == "approver2" else role))
                self.users[role] = u.id
                self.tokens[role] = create_token(u.id, org.id)
            db.commit()

    def h(self, role: str = "org_admin") -> dict:
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    def req(self, method: str, url: str, role: str = "org_admin", expect: int | None = 200, **kw):
        r = self.client.request(method, url, headers=self.h(role), **kw)
        if expect is not None:
            assert r.status_code == expect, f"{method} {url} -> {r.status_code}: {r.text[:600]}"
        return r

    def get(self, url, role="org_admin", **kw):
        return self.req("GET", url, role, **kw).json()

    def post(self, url, role="org_admin", expect=200, **kw):
        return self.req("POST", url, role, expect, **kw)

    def drain(self):
        jobs.run_pending()


@pytest.fixture
def org(client):
    return Org(client, f"SYNTHETIC Org {uuid.uuid4().hex[:6]}")


@pytest.fixture
def other_org(client):
    return Org(client, f"SYNTHETIC Other {uuid.uuid4().hex[:6]}")
