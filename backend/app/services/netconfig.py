"""Network access: let other computers in the office use the TenderPilot installed on a server.

Off by default (TenderPilot then listens on 127.0.0.1 only). When an administrator turns it on (Settings > Network
access, or the installer option), the launcher listens on all network addresses of the server, over HTTPS with a
certificate TenderPilot makes for the server's own names and addresses. The setting lives in DATA_DIR/server.json and
takes effect when TenderPilot restarts.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import DATA_DIR

CONFIG = DATA_DIR / "server.json"
TLS_DIR = DATA_DIR / "tls"
CERT = TLS_DIR / "tenderpilot.crt"
KEY = TLS_DIR / "tenderpilot.key"
DEFAULT_PORT = 8471
DEFAULTS = {"network": False, "port": DEFAULT_PORT, "https": True}


def read_config() -> dict:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = {**DEFAULTS, **{k: data[k] for k in DEFAULTS if k in data}}
    out["network"], out["https"] = bool(out["network"]), bool(out["https"])
    try:
        out["port"] = int(out["port"])
    except (TypeError, ValueError):
        out["port"] = DEFAULT_PORT
    if not 1024 <= out["port"] <= 65535:
        out["port"] = DEFAULT_PORT
    return out


def write_config(network: bool, port: int, https: bool) -> dict:
    if not 1024 <= int(port) <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    data = {"network": bool(network), "port": int(port), "https": bool(https)}
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)
    return data


def active() -> dict:
    """How this running TenderPilot was started (set by the launcher)."""
    return {"network": os.environ.get("TP_NETWORK_ACTIVE") == "1", "https": os.environ.get("TP_HTTPS_ACTIVE") == "1",
            "port": int(os.environ.get("TP_PORT") or DEFAULT_PORT)}


def addresses() -> dict:
    """The server's names and IPv4 addresses that staff can type in their browser."""
    host = socket.gethostname()
    names = [host]
    try:
        fqdn = socket.getfqdn()
        if fqdn and fqdn != host and "." in fqdn:
            names.append(fqdn)
    except OSError:
        pass
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    try:  # the address used for the default route (works without DNS)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return {"names": names, "ips": ips}


def urls(port: int, https: bool) -> list[str]:
    a = addresses()
    scheme = "https" if https else "http"
    return [f"{scheme}://{h}:{port}" for h in a["names"] + a["ips"]]


def firewall_command(port: int) -> str:
    return (f'New-NetFirewallRule -DisplayName "TenderPilot {port}" -Direction Inbound -Protocol TCP -LocalPort {port} '
            f"-Action Allow -Profile Domain,Private")


def cert_info() -> dict | None:
    if not CERT.exists():
        return None
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    c = x509.load_pem_x509_certificate(CERT.read_bytes())
    try:
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(n) for n in san.get_values_for_type(x509.DNSName)] + [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names = []
    return {"names": names, "valid_until": c.not_valid_after_utc.date().isoformat(),
            "fingerprint_sha256": c.fingerprint(hashes.SHA256()).hex(":").upper()}


def ensure_cert(force: bool = False) -> Path:
    """Self-signed certificate for the server's names and addresses (RSA 2048, 5 years). Kept until regenerated."""
    a = addresses()
    if CERT.exists() and KEY.exists() and not force:
        info = cert_info() or {}
        if set(a["names"] + a["ips"]) <= set(info.get("names", [])):
            return CERT  # still covers every current name and address
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = a["names"][0]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TenderPilot (this server)")])
    alt = [x509.DNSName(n) for n in dict.fromkeys(a["names"] + ["localhost"])]
    alt += [x509.IPAddress(ipaddress.ip_address(i)) for i in dict.fromkeys(a["ips"] + ["127.0.0.1"])]
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=5 * 365))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    KEY.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                      serialization.NoEncryption()))
    try:
        os.chmod(KEY, 0o600)
    except OSError:
        pass
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return CERT
