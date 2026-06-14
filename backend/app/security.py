"""Security primitives: password hashing, JWT issue/verify, and per-user credential encryption.

Credentials are encrypted with Fernet using a key derived per-user (PBKDF2-HMAC over the
master key with the user id as salt). The master key never leaves the server; decrypted
secrets are only ever held in memory at connection time.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import get_settings

settings = get_settings()


# --- Passwords -------------------------------------------------------------
# We use bcrypt directly (passlib is effectively unmaintained and breaks against
# modern bcrypt releases). bcrypt hashes only the first 72 bytes of the input, so we
# truncate explicitly to keep behaviour well-defined for long passwords.

_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(plain), hashed.encode("utf-8"))
    except ValueError:
        return False


# --- JWT -------------------------------------------------------------------

def create_access_token(subject: str, *, refresh: bool = False) -> str:
    now = datetime.now(UTC)
    if refresh:
        expire = now + timedelta(days=settings.refresh_token_expire_days)
        token_type = "refresh"
    else:
        expire = now + timedelta(minutes=settings.access_token_expire_minutes)
        token_type = "access"
    payload = {"sub": subject, "type": token_type, "iat": now, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# --- High-entropy single-use tokens (password reset, etc.) -----------------

def generate_url_token(n_bytes: int = 32) -> str:
    """Generate a cryptographically-random URL-safe token."""
    return secrets.token_urlsafe(n_bytes)


def hash_token(token: str) -> str:
    """Hash a high-entropy token for at-rest storage (SHA-256 hex).

    A fast hash is appropriate here (unlike passwords): the token already carries
    full entropy, so it needs no slow KDF, and storing only the hash means a database
    leak never exposes a usable token.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Per-user credential encryption ---------------------------------------

def _derive_user_fernet(user_id: str) -> Fernet:
    """Derive a per-user Fernet from the master key + user id salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=user_id.encode("utf-8"),
        iterations=200_000,
    )
    raw_master = settings.encryption_key.encode("utf-8")
    key = base64.urlsafe_b64encode(kdf.derive(raw_master))
    return Fernet(key)


def encrypt_for_user(user_id: str, plaintext: str) -> bytes:
    return _derive_user_fernet(user_id).encrypt(plaintext.encode("utf-8"))


def decrypt_for_user(user_id: str, token: bytes) -> str:
    return _derive_user_fernet(user_id).decrypt(token).decode("utf-8")
