"""Fernet symmetric encryption for sensitive data (passwords, tokens).

Supports key rotation via MultiFernet: set STUDIO_ENCRYPTION_KEY to a comma-separated
list of keys (newest first). Encryption always uses the first (newest) key; decryption
tries all keys in order. To rotate: generate a new key, prepend it to the existing
key(s), then re-encrypt all data at your convenience.
"""

import logging
import os

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | MultiFernet | None = None


def _is_production() -> bool:
    """Check if running in production mode.

    Explicit STUDIO_ENV takes priority: "dev"/"development" → not production.
    Falls back to Docker detection (/.dockerenv) only when STUDIO_ENV is unset.
    """
    env = os.getenv("STUDIO_ENV", "").lower()
    if env in ("dev", "development"):
        return False
    if env in ("prod", "production"):
        return True
    return os.path.exists("/.dockerenv")


def _get_fernet() -> Fernet | MultiFernet:
    """Get or create Fernet/MultiFernet instance.

    In production: requires STUDIO_ENCRYPTION_KEY to be set (refuses to start otherwise).
    In development: auto-generates a key with a warning.

    Supports comma-separated keys for key rotation via MultiFernet.
    The first key is used for encryption; all keys are tried for decryption.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    key = settings.encryption_key.strip()
    # Guard against inline comments leaking from .env (e.g. "# comment")
    if not key or key.startswith("#"):
        if _is_production():
            raise RuntimeError(
                "STUDIO_ENCRYPTION_KEY is required in production. "
                "Generate one with: python -c "
                "'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'"
            )
        key = Fernet.generate_key().decode()
        logger.warning(
            "STUDIO_ENCRYPTION_KEY is empty — a key was auto-generated. "
            "Set STUDIO_ENCRYPTION_KEY in .env to persist encrypted data across restarts.",
        )
        settings.encryption_key = key

    # Support comma-separated keys for MultiFernet key rotation
    key_parts = [k.strip() for k in key.split(",") if k.strip()]
    if len(key_parts) > 1:
        fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in key_parts]
        _fernet = MultiFernet(fernets)
        logger.info("MultiFernet initialized with %d keys (key rotation enabled)", len(fernets))
    else:
        single_key = key_parts[0]
        _fernet = Fernet(single_key.encode() if isinstance(single_key, str) else single_key)

    return _fernet


def generate_key() -> str:
    """Generate a new Fernet encryption key."""
    return Fernet.generate_key().decode()


def encrypt(plaintext: str) -> str:
    """Encrypt plaintext string and return base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt base64-encoded ciphertext and return plaintext string."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# Extra-params selective encryption (for sensitive fields like credentials_json)
# ---------------------------------------------------------------------------

_ENC_PREFIX = "__enc__:"


def encrypt_sensitive_extra(extra_params: dict | None) -> dict | None:
    """Encrypt sensitive fields within extra_params dict.

    Sensitive field names are defined in ``db_registry.SENSITIVE_EXTRA_FIELDS``.
    Encrypted values are stored with a ``__enc__:`` prefix so that we can
    distinguish them from plaintext on read (backward-compatible).
    """
    if not extra_params:
        return extra_params

    from app.db_registry import SENSITIVE_EXTRA_FIELDS

    result = dict(extra_params)
    for key in SENSITIVE_EXTRA_FIELDS:
        value = result.get(key)
        if value is None:
            continue
        # Convert dicts/non-strings to JSON string before encrypting
        if not isinstance(value, str):
            import json

            value = json.dumps(value)
        # Don't double-encrypt
        if value.startswith(_ENC_PREFIX):
            continue
        result[key] = _ENC_PREFIX + encrypt(value)
    return result


def decrypt_sensitive_extra(extra_params: dict | None) -> dict | None:
    """Decrypt sensitive fields within extra_params dict.

    Handles both encrypted (``__enc__:`` prefixed) and plaintext values
    for backward compatibility with data created before encryption was added.
    """
    if not extra_params:
        return extra_params

    from app.db_registry import SENSITIVE_EXTRA_FIELDS

    result = dict(extra_params)
    for key in SENSITIVE_EXTRA_FIELDS:
        value = result.get(key)
        if value is None or not isinstance(value, str):
            continue
        if value.startswith(_ENC_PREFIX):
            result[key] = decrypt(value[len(_ENC_PREFIX) :])
    return result


def mask_sensitive_extra(extra_params: dict | None) -> dict | None:
    """Replace sensitive extra_params values with a mask for API responses.

    Works with both encrypted and plaintext values.
    """
    if not extra_params:
        return extra_params

    from app.db_registry import SENSITIVE_EXTRA_FIELDS

    result = dict(extra_params)
    for key in SENSITIVE_EXTRA_FIELDS:
        if key in result and result[key]:
            result[key] = "••••••"
    return result
