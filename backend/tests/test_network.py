"""Network access for a server install: settings, certificate, setup guard, sign-in protection, staff accounts."""
from __future__ import annotations

import ipaddress
import socket

import pytest
from cryptography import x509
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.db import SessionLocal
from app.main import app
from app.models import User
from app.security import hash_password
from app.services import netconfig


@pytest.fixture
def tmp_net(monkeypatch, tmp_path):
    monkeypatch.setattr(netconfig, "CONFIG", tmp_path / "server.json")
    monkeypatch.setattr(netconfig, "TLS_DIR", tmp_path / "tls")
    monkeypatch.setattr(netconfig, "CERT", tmp_path / "tls" / "tenderpilot.crt")
    monkeypatch.setattr(netconfig, "KEY", tmp_path / "tls" / "tenderpilot.key")
    return tmp_path


def test_config_defaults_and_validation(tmp_net):
    assert netconfig.read_config() == {"network": False, "port": 8471, "https": True}
    netconfig.write_config(True, 9443, True)
    assert netconfig.read_config() == {"network": True, "port": 9443, "https": True}
    (tmp_net / "server.json").write_text('{"network": true, "port": 80}')  # bad port falls back
    assert netconfig.read_config()["port"] == 8471
    (tmp_net / "server.json").write_text("not json")
    assert netconfig.read_config()["network"] is False
    with pytest.raises(ValueError):
        netconfig.write_config(True, 80, True)


def test_certificate_covers_the_server_names_and_is_kept(tmp_net):
    netconfig.ensure_cert()
    cert = x509.load_pem_x509_certificate(netconfig.CERT.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert socket.gethostname() in san.get_values_for_type(x509.DNSName)
    assert ipaddress.ip_address("127.0.0.1") in san.get_values_for_type(x509.IPAddress)
    fp = netconfig.cert_info()["fingerprint_sha256"]
    netconfig.ensure_cert()
    assert netconfig.cert_info()["fingerprint_sha256"] == fp  # not regenerated without need
    netconfig.ensure_cert(force=True)
    assert netconfig.cert_info()["fingerprint_sha256"] != fp
    assert b"PRIVATE KEY" in netconfig.KEY.read_bytes()


def test_network_settings_are_for_administrators(org, tmp_net):
    org.req("GET", "/api/v1/network", role="tender_manager", expect=403)
    s = org.get("/api/v1/network")
    assert s["config"]["network"] is False and s["restart_needed"] is False and s["can_restart"] is False
    out = org.req("PUT", "/api/v1/network", json={"network": True, "port": 8471, "https": True}).json()
    assert out["config"]["network"] is True and out["restart_needed"] is True
    assert out["certificate"] and any(u.startswith("https://") and u.endswith(":8471") for u in out["urls"])
    assert "New-NetFirewallRule" in out["firewall_command"] and "8471" in out["firewall_command"]
    # staff can download the certificate to trust it; the private key is never served
    r = org.req("GET", "/api/v1/network/certificate", role="estimator")
    assert b"BEGIN CERTIFICATE" in r.content and b"PRIVATE" not in r.content
    org.req("PUT", "/api/v1/network", json={"network": True, "port": 80}, expect=422)


def test_demo_is_never_opened_to_the_network(org, tmp_net, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    org.req("PUT", "/api/v1/network", json={"network": True, "port": 8471, "https": True}, expect=409)


def test_first_setup_only_from_the_server_itself(monkeypatch):
    monkeypatch.setenv("TP_NETWORK_ACTIVE", "1")
    body = {"org_name": "Outside Co", "name": "X", "email": "x@x.test", "password": "password-123456"}
    with TestClient(app, client=("192.168.1.50", 50000)) as remote:
        r = remote.post("/api/v1/setup", json=body)
        assert r.status_code == 403 and "on the server itself" in r.json()["detail"]
    with TestClient(app) as local:  # local: refused only because this test database is already set up
        assert local.post("/api/v1/setup", json=body).status_code == 409


def _user(email: str, password: str) -> int:
    with SessionLocal() as db:
        u = User(email=email, name="Staff", password_hash=hash_password(password))
        db.add(u)
        db.commit()
        return u.id


def test_wrong_passwords_block_sign_in_for_a_while(client):
    auth_api._fails.clear()
    email = "throttle@example.test"
    uid = _user(email, "right-password-1")
    from app.models import Membership, Organization
    with SessionLocal() as db:
        o = Organization(name="Throttle Org")
        db.add(o)
        db.flush()
        db.add(Membership(org_id=o.id, user_id=uid, role="estimator"))
        db.commit()
    for _ in range(auth_api.FAIL_LIMIT):
        assert client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "right-password-1"})
    assert r.status_code == 429  # blocked even with the right password
    auth_api._fails.clear()
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "right-password-1"}).status_code == 200


def test_administrator_manages_staff_accounts(org, client):
    auth_api._fails.clear()
    new = org.post("/api/v1/users", json={"name": "Site Staff", "email": "staff1@example.test", "password": "first-password-1",
                                          "roles": ["document_coordinator"]}).json()
    login = lambda pw: client.post("/api/v1/auth/login", json={"email": "staff1@example.test", "password": pw})  # noqa: E731
    tok = login("first-password-1").json()["token"]
    # reset a forgotten password
    org.req("PATCH", f"/api/v1/users/{new['id']}", json={"password": "second-password-2"})
    assert login("first-password-1").status_code == 401 and login("second-password-2").status_code == 200
    # switch the account off: sign-in and existing sessions stop at once
    org.req("PATCH", f"/api/v1/users/{new['id']}", json={"active": False})
    assert login("second-password-2").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    org.req("PATCH", f"/api/v1/users/{new['id']}", json={"active": True})
    # only administrators; never yourself; never another organisation's user
    org.req("PATCH", f"/api/v1/users/{new['id']}", json={"active": False}, role="tender_manager", expect=403)
    org.req("PATCH", f"/api/v1/users/{org.users['org_admin']}", json={"active": False}, expect=409)
    org.req("PATCH", "/api/v1/users/999999", json={"active": False}, expect=404)


def test_staff_change_their_own_password(client):
    auth_api._fails.clear()
    uid = _user("self@example.test", "old-password-12")
    from app.models import Membership, Organization
    with SessionLocal() as db:
        o = Organization(name="Self Org")
        db.add(o)
        db.flush()
        db.add(Membership(org_id=o.id, user_id=uid, role="estimator"))
        db.commit()
    tok = client.post("/api/v1/auth/login", json={"email": "self@example.test", "password": "old-password-12"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/v1/me/password", headers=h, json={"current_password": "nope", "new_password": "new-password-12"}).status_code == 403
    assert client.post("/api/v1/me/password", headers=h, json={"current_password": "old-password-12", "new_password": "new-password-12"}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "self@example.test", "password": "new-password-12"}).status_code == 200
