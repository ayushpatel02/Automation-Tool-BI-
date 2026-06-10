"""Auth request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: EmailStr


class ApiKeyRequest(BaseModel):
    provider: str
    # Empty string clears the stored key for the provider.
    api_key: str = ""


class ApiKeyStatus(BaseModel):
    """Whether a provider has a key on file — the key itself is never returned."""

    provider: str
    configured: bool
