"""Connector endpoints: test, save (encrypted), list, delete, and profile a schema."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import get_settings
from app.connectors import get_connector
from app.db import get_db
from app.models import StoredCredential, User
from app.profiler import profile_schema
from app.schemas.connector import (
    ConnectionTestResult,
    ConnectorConfig,
    SchemaProfile,
    StoredCredentialResponse,
)
from app.security import decrypt_for_user, encrypt_for_user

router = APIRouter(prefix="/connectors", tags=["connectors"])

_ALLOWED_UPLOAD_EXTENSIONS = {".csv": "csv", ".xlsx": "excel", ".xls": "excel"}
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


@router.post("/test", response_model=ConnectionTestResult)
async def test_connector(
    config: ConnectorConfig, user: User = Depends(get_current_user)
) -> ConnectionTestResult:
    connector = get_connector(config)
    return await connector.test_connection()


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict:
    """Persist an uploaded CSV/Excel file in the user's upload dir and return its path."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or 'none'}'. Allowed: .csv, .xlsx, .xls",
        )

    settings = get_settings()
    upload_dir = settings.generated_dir / "uploads" / user.id
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex}{ext}"
    dest = upload_dir / safe_name

    total = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > _MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
                )
            out.write(chunk)

    return {
        "file_path": str(dest.resolve()),
        "original_name": file.filename,
        "size_bytes": total,
        "connector_type": _ALLOWED_UPLOAD_EXTENSIONS[ext],
    }


@router.post("", response_model=StoredCredentialResponse, status_code=201)
async def save_connector(
    config: ConnectorConfig,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StoredCredentialResponse:
    encrypted = encrypt_for_user(user.id, config.model_dump_json())
    cred = StoredCredential(
        user_id=user.id,
        name=config.name,
        connector_type=config.type.value,
        encrypted_json=encrypted,
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return StoredCredentialResponse(
        id=cred.id,
        name=cred.name,
        connector_type=cred.connector_type,
        created_at=cred.created_at,
    )


@router.get("", response_model=list[StoredCredentialResponse])
async def list_connectors(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[StoredCredentialResponse]:
    rows = (
        await db.execute(
            select(StoredCredential).where(StoredCredential.user_id == user.id)
        )
    ).scalars()
    return [
        StoredCredentialResponse(
            id=c.id, name=c.name, connector_type=c.connector_type, created_at=c.created_at
        )
        for c in rows
    ]


@router.delete("/{credential_id}", status_code=204)
async def delete_connector(
    credential_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    cred = await _load_owned_credential(credential_id, user, db)
    await db.delete(cred)
    await db.commit()


@router.post("/{credential_id}/profile", response_model=SchemaProfile)
async def profile_connector(
    credential_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SchemaProfile:
    config = await load_connector_config(credential_id, user, db)
    connector = get_connector(config)
    test = await connector.test_connection()
    if not test.ok:
        raise HTTPException(status_code=400, detail=f"Connection failed: {test.message}")
    return await profile_schema(connector)


# --- helpers reused by the sessions API ------------------------------------

async def _load_owned_credential(
    credential_id: str, user: User, db: AsyncSession
) -> StoredCredential:
    cred = (
        await db.execute(
            select(StoredCredential).where(StoredCredential.id == credential_id)
        )
    ).scalar_one_or_none()
    if cred is None or cred.user_id != user.id:
        raise HTTPException(status_code=404, detail="Credential not found")
    return cred


async def load_connector_config(
    credential_id: str, user: User, db: AsyncSession
) -> ConnectorConfig:
    cred = await _load_owned_credential(credential_id, user, db)
    decrypted = decrypt_for_user(user.id, cred.encrypted_json)
    return ConnectorConfig(**json.loads(decrypted))
