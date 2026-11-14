"""Updates must never change existing data: pre-update copy, additive migrations, no silent re-reading."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app import migrations
from app.services import jobs
from fixtures.synthetic import nit_pdf

from helpers import reqs, tender, upload


def test_database_is_copied_before_an_update(org):
    tender(org)  # make sure the test database holds data
    dest = migrations.backup_before_update(["9999_future"])
    assert dest is not None and Path(dest).exists()
    con = sqlite3.connect(str(dest))
    try:
        assert con.execute("SELECT count(*) FROM tenders").fetchone()[0] >= 1
    finally:
        con.close()


def test_no_backup_when_nothing_is_pending():
    assert migrations.backup_before_update([]) is None


def test_every_migration_after_0003_is_listed_once():
    ids = [m for m, _ in migrations.MIGRATIONS]
    assert len(ids) == len(set(ids)) and ids[:3] == ["0001_baseline", "0002_integrations", "0003_similarity_links"]
    assert migrations.pending_migrations() == []


def test_reader_upgrade_does_not_reread_old_tenders_unless_asked(org, monkeypatch):
    t = tender(org)
    upload(org, t["id"], nit_pdf(), "nit.pdf", "nit")
    before = reqs(org, t["id"])
    assert before
    # Simulate a newer tender reader being installed.
    monkeypatch.setattr(jobs, "READER_VERSION", "reader-test-next")
    status = org.get(f"/api/v1/tenders/{t['id']}/reread")
    assert len(status["outdated"]) == 1
    # Nothing is queued by itself (start-up re-reading is off by default).
    org.drain()
    assert reqs(org, t["id"]) == before
    assert len(org.get(f"/api/v1/tenders/{t['id']}/reread")["outdated"]) == 1
    # Only when the user asks:
    assert org.post(f"/api/v1/tenders/{t['id']}/reread").json()["queued"] == 1
    org.drain()
    assert org.get(f"/api/v1/tenders/{t['id']}/reread")["outdated"] == []
    # Reviewers without document rights cannot trigger it.
    org.post(f"/api/v1/tenders/{t['id']}/reread", role="auditor", expect=403)
