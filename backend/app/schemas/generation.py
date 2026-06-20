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


# --- Self-test (pre-flight) ------------------------------------------------

# How a finding can be resolved: a deterministic normalizer ("auto"), an LLM repair
# pass ("llm"), or a human ("manual").
FixKind = Literal["auto", "llm", "manual"]
# Which test layer surfaced the finding.
TestLayer = Literal["deterministic", "te2", "llm_review"]
# For an "llm" fix, which artifact the repair prompt must target: the TMDL semantic
# model or the PBIR report. None lets the repair router infer it from the category.
RepairTarget = Literal["model", "report"]


class PreflightFinding(BaseModel):
    """A single issue found while self-testing a generated report before handing it over."""

    category: str  # e.g. "tmdl.indentation", "structure.missing_file", "pbir.cross_ref"
    message: str
    severity: Literal["error", "warning"] = "error"
    file: str = ""
    fix: FixKind = "manual"
    layer: TestLayer = "deterministic"
    # Which artifact an "llm" fix belongs to. Set by the LLM self-review (which knows
    # whether an issue is about a DAX measure or a visual); None for the static layers,
    # where the repair router infers the target from the category.
    repair_target: RepairTarget | None = None


class SelfTestReport(BaseModel):
    """Aggregated result of running the self-test (one or more layers) on a report."""

    passed: bool
    layers_run: list[TestLayer] = Field(default_factory=list)
    findings: list[PreflightFinding] = Field(default_factory=list)
    # Human-readable description of every fix the auto-repair loop applied.
    auto_fixed: list[str] = Field(default_factory=list)
    attempts: int = 1

    @property
    def errors(self) -> list[PreflightFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[PreflightFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @classmethod
    def from_findings(
        cls,
        findings: list[PreflightFinding],
        *,
        layers_run: list[TestLayer],
        auto_fixed: list[str] | None = None,
        attempts: int = 1,
    ) -> SelfTestReport:
        passed = not any(f.severity == "error" for f in findings)
        return cls(
            passed=passed,
            layers_run=layers_run,
            findings=findings,
            auto_fixed=auto_fixed or [],
            attempts=attempts,
        )


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
