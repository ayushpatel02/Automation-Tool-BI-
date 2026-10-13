"""Password-reset token lifecycle: issue + email a token, and verify + consume one.

Tokens are single-use, time-limited, and stored only as SHA-256 hashes. The request
flow is deliberately non-enumerating: it behaves identically whether or not the email
maps to a real account.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PasswordResetToken, User
from app.security import generate_url_token, hash_password, hash_token
from app.services.email import send_password_reset_email

settings = get_settings()


def _reset_url(token: str) -> str:
    base = settings.frontend_origin.rstrip("/")
    return f"{base}/Reset_Password?token={quote(token)}"


async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Issue a reset token for ``email`` and email it.

    Silently does nothing if no such user exists, so the endpoint can't be used to
    discover which emails are registered.
    """
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        return

    # Invalidate any outstanding tokens so only the newest link works.
    await db.execute(
        delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    )

    token = generate_url_token()
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.password_reset_expire_minutes
    )
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    await db.commit()

    await send_password_reset_email(user.email, token, _reset_url(token))


async def reset_password(db: AsyncSession, token: str, new_password: str) -> bool:
    """Consume ``token`` and set the user's new password.

    Returns False if the token is unknown, already used, or expired.
    """
    record = (
        await db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == hash_token(token)
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if record is None or record.used_at is not None:
        return False

    # Datetimes can come back from SQLite without tzinfo; treat those as UTC.
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < now:
        return False

    user = (
        await db.execute(select(User).where(User.id == record.user_id))
    ).scalar_one_or_none()
    if user is None:
        return False

    user.hashed_password = hash_password(new_password)
    record.used_at = now
    await db.commit()
    return True
