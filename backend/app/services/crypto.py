"""Symmetric encryption for secrets stored at rest (e.g. cluster kubeconfigs).

Uses Fernet (AES-128-CBC + HMAC). The key comes from ``APP_ENCRYPTION_KEY`` when
set; otherwise it is derived deterministically from ``session_secret_key`` so the
portal works out of the box. A dedicated ``APP_ENCRYPTION_KEY`` is recommended in
production — rotating it invalidates previously stored ciphertext.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _derive_key() -> bytes:
    """Return a urlsafe-base64 32-byte Fernet key.

    Prefers an explicit ``APP_ENCRYPTION_KEY`` (already a Fernet key). Falls back
    to a SHA-256 digest of ``session_secret_key`` so encryption is always
    available, logging a warning so operators know to set a dedicated key.
    """
    explicit = settings.encryption_key
    if explicit:
        # Accept either a ready Fernet key or arbitrary text we normalize.
        try:
            Fernet(explicit.encode())
            return explicit.encode()
        except (ValueError, TypeError):
            digest = hashlib.sha256(explicit.encode()).digest()
            return base64.urlsafe_b64encode(digest)
    logger.warning(
        "APP_ENCRYPTION_KEY not set; deriving encryption key from session_secret_key. "
        "Set a dedicated APP_ENCRYPTION_KEY in production."
    )
    digest = hashlib.sha256(settings.session_secret_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_derive_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string, returning a urlsafe token."""
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt`.

    Raises ``InvalidToken`` if the ciphertext was produced with a different key
    (e.g. the encryption key was rotated).
    """
    return _cipher().decrypt(token.encode()).decode()


__all__ = ["encrypt", "decrypt", "InvalidToken"]
