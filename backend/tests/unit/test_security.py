"""Password hashing, JWT, and per-user encryption round-trips."""

import pytest

from app.security import (
    create_access_token,
    decode_token,
    decrypt_for_user,
    encrypt_for_user,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("hunter2!")
    assert verify_password("hunter2!", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    token = create_access_token("user-123")
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_refresh_token_type():
    token = create_access_token("user-123", refresh=True)
    assert decode_token(token)["type"] == "refresh"


def test_encryption_roundtrip():
    secret = '{"password": "s3cr3t"}'
    blob = encrypt_for_user("user-abc", secret)
    assert blob != secret.encode()
    assert decrypt_for_user("user-abc", blob) == secret


def test_encryption_is_per_user():
    from cryptography.fernet import InvalidToken

    blob = encrypt_for_user("user-a", "data")
    # A different user's key must not decrypt another user's blob.
    with pytest.raises(InvalidToken):
        decrypt_for_user("user-b", blob)
