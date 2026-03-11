"""TOTP 2FA service — secret generation, QR codes, verification, recovery codes."""

import base64
import io
import json
import logging
import secrets

import bcrypt
import pyotp
import qrcode  # type: ignore[import-untyped]

from app.services.crypto import decrypt, encrypt

logger = logging.getLogger(__name__)

_ISSUER = "SMKRV MCP Studio"
_RECOVERY_CODE_COUNT = 8
_RECOVERY_CODE_LENGTH = 12  # hex chars per code (48 bits entropy)
_BCRYPT_ROUNDS = 12


# --- TOTP secret ---


def generate_totp_secret() -> str:
    """Generate a new random TOTP secret (base32, 32 bytes)."""
    return pyotp.random_base32(32)


def encrypt_totp_secret(secret: str) -> str:
    """Encrypt a TOTP secret for database storage using Fernet."""
    return encrypt(secret)


def decrypt_totp_secret(encrypted: str) -> str:
    """Decrypt a TOTP secret from database storage."""
    return decrypt(encrypted)


# --- QR code ---


def generate_qr_data_uri(secret: str, username: str) -> str:
    """Generate a QR code as a base64 data URI for TOTP provisioning.

    Compatible with Google Authenticator, Authy, 1Password, etc.
    """
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=username, issuer_name=_ISSUER)

    img = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


# --- TOTP verification ---


def verify_totp_code(secret: str, code: str) -> bool:
    """Verify a TOTP code against a secret. Allows 1 window of clock drift (~90s)."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


# --- Recovery codes ---


def generate_recovery_codes() -> list[str]:
    """Generate a set of plaintext recovery codes (8 hex chars each, uppercase)."""
    return [
        secrets.token_hex(_RECOVERY_CODE_LENGTH // 2).upper()
        for _ in range(_RECOVERY_CODE_COUNT)
    ]


def hash_recovery_codes(codes: list[str]) -> str:
    """Hash each recovery code with bcrypt and return JSON-serialized list."""
    hashed = []
    for code in codes:
        salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
        h = bcrypt.hashpw(code.encode(), salt).decode()
        hashed.append(h)
    return json.dumps(hashed)


def verify_and_consume_recovery_code(
    code: str, hashed_json: str
) -> tuple[bool, str | None]:
    """Check a recovery code against hashed list.

    Returns (matched, updated_hashed_json).
    If matched, the consumed code is removed from the list.
    """
    hashed_list: list[str] = json.loads(hashed_json)
    normalized = code.strip().upper()
    for i, h in enumerate(hashed_list):
        if bcrypt.checkpw(normalized.encode(), h.encode()):
            hashed_list.pop(i)
            return True, json.dumps(hashed_list)
    return False, None


def count_remaining_recovery_codes(hashed_json: str | None) -> int:
    """Count remaining (unused) recovery codes."""
    if not hashed_json:
        return 0
    hashed_list: list[str] = json.loads(hashed_json)
    return len(hashed_list)
