"""Schemas for the error-diagnosis helper."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Base64 expands ~4/3; this caps the decoded image around 5 MB.
_MAX_IMAGE_B64_CHARS = 5 * 1024 * 1024 * 4 // 3


class DiagnoseRequest(BaseModel):
    model_id: str
    # Either error_text or an attached screenshot (image_base64) must be provided.
    error_text: str = Field(default="", max_length=8000)
    # Optional extra context (what the user was doing, the original report request, etc.).
    context: str | None = Field(default=None, max_length=4000)
    # Optional screenshot of the error (e.g. a Power BI Desktop dialog), base64-encoded.
    image_base64: str | None = Field(default=None)
    image_media_type: str | None = Field(default=None, max_length=100)

    @field_validator("image_base64")
    @classmethod
    def _check_image_size(cls, v: str | None) -> str | None:
        if v is not None and len(v) > _MAX_IMAGE_B64_CHARS:
            raise ValueError("Image is too large (max 5 MB)")
        return v


class DiagnoseResponse(BaseModel):
    answer: str
    model_id: str
