"""Encryption at rest for credentials entered in Settings (e.g. the AI provider API key).

Windows: DPAPI (CryptProtectData, current-user scope) with the server secret as extra entropy, so
the ciphertext is useless without BOTH this Windows account and backend/data/.secret_key.
Elsewhere: Fernet with a key derived from the server secret.

Plaintext never touches the database, logs, audit events or API responses.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import re

from ..config import settings


class SecretUnavailable(Exception):
    """Stored secret exists but cannot be decrypted here (different Windows user or secret key)."""


def _entropy() -> bytes:
    return hashlib.sha256(b"tenderpilot-secretbox|" + settings.secret_key.encode()).digest()


if os.name == "nt":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _to_blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _dpapi(data: bytes, protect: bool) -> bytes:
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        inp, _b1 = _to_blob(data)
        ent, _b2 = _to_blob(_entropy())
        out = _Blob()
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))  # UI_FORBIDDEN
        if not ok:
            raise SecretUnavailable("Windows could not decrypt the stored credential (different user account or secret key).")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(_entropy()))


def scheme() -> str:
    return "Windows DPAPI (current user)" if os.name == "nt" else "Fernet (server secret key)"


def encrypt(plaintext: str) -> str:
    raw = plaintext.encode()
    if os.name == "nt":
        return "dpapi1:" + base64.b64encode(_dpapi(raw, True)).decode()
    return "fernet1:" + _fernet().encrypt(raw).decode()


def decrypt(token: str) -> str:
    prefix, _, body = token.partition(":")
    try:
        if prefix == "dpapi1" and os.name == "nt":
            return _dpapi(base64.b64decode(body), False).decode()
        if prefix == "fernet1":
            return _fernet().decrypt(body.encode()).decode()
    except SecretUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - never include ciphertext/plaintext in the message
        raise SecretUnavailable(f"Stored credential could not be decrypted ({type(exc).__name__}).") from None
    raise SecretUnavailable("Stored credential uses an encryption scheme not available on this computer.")


def hint(plaintext: str) -> str:
    """Last four characters only, for display."""
    return plaintext[-4:] if len(plaintext) >= 12 else ""


_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}")


def redact(text: str | None) -> str | None:
    """Defensive scrub for anything that might echo a key (error messages from libraries)."""
    return _KEY_RE.sub("sk-ant-...(redacted)", text) if text else text
