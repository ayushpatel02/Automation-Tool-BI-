"""Generation pipeline domain models: artifacts, validation, requests."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- Generated artifacts ---------------------------------------------------

class SemanticModelArtifacts(BaseModel):
    """TMDL files as raw text, keyed by relative path under definition/."""

    model_tmdl: str
    tables: dict[str, str] = Field(default_factory=dict)  # "Sales.tmdl" -> content
    relationships_tmdl: str = ""
    expressions_tmdl: str = ""


class ReportArtifacts(BaseModel):
    """PBIR JSON objects."""

    report_json: dict
    pages: list[dict] = Field(default_factory=list)  # [{page_id, page_json, visuals:[...]}]


# --- Validation ------------------------------------------------------------

class ValidationError(BaseModel):
    file: str
    path: str = ""
    message: str
    severity: Literal["error", "warning"] = "error"


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationError] = Field(default_factory=list)

    @classmethod
    def ok(cls) -> ValidationResult:
        return cls(valid=True)


# --- API requests / responses ---------------------------------------------

class GenerateRequest(BaseModel):
    model_id: str
    request: str
    credential_id: str | None = None
    # Multiple stored credentials, profiled and merged into one multi-source schema.
    credential_ids: list[str] | None = None
    # Inline connector config (alternative to a stored credential, e.g. file uploads).
    connector: dict | None = None
    # Multiple inline connector configs, merged alongside any credential_ids.
    connectors: list[dict] | None = None
    project_name: str = "GeneratedReport"


class RefineRequest(BaseModel):
    message: str


class SessionResponse(BaseModel):
    id: str
    status: str
    model_id: str
    original_request: str
    project_name: str | None = None
    error_message: str | None = None
    has_download: bool = False
