# AI-Powered Power BI Report Generator — Phase 1 + 2 Implementation Plan

## Context

The goal is a hosted web tool where a user connects a data source, describes a report in natural language, and receives a valid `.pbip` Power BI project they can open in Power BI Desktop and refine via AI chat. The generation layer is an orchestration shell — the LLM produces the actual TMDL and PBIR artifacts as structured text.

Key constraints established:
- **UI:** Streamlit web app + FastAPI REST backend (decoupled, both containerised)
- **Deployment:** Self-hosted web service (multi-user, JWT auth)
- **Team:** 3–4 engineers, ~6–8 weeks for a solid v1
- **Starting model:** Google Gemini (API access confirmed); Claude and GPT-4o added later via config
- **Power BI experience:** Basic — plan must not assume deep PBI knowledge

---

## Verified Technical Facts (June 2026, checked against live docs)

- **PBIR is the default** in Power BI Service since **Jan 25 2026**; **Power BI Desktop default from March 2026** (still in preview — users must have Desktop ≥ March 2026 and PBIR preview enabled).
- **Every PBIR file carries a public `$schema` URL** following the pattern `https://developer.microsoft.com/json-schemas/fabric/item/report/definition/{fileType}/{version}/schema.json`. Python `jsonschema` validates all PBIR output against these — no .NET needed for the report layer.
- **TMDL has no pure-Python validator.** Microsoft's `tmdl-parser` is TypeScript. Semantic-model validation requires shelling out to **Tabular Editor 2 CLI** or **`pbi-tools compile`** (both are .NET-based tools that run cross-platform under .NET Runtime). This is the single biggest validation gap.
- **Gemini API is mid-migration this month (June 2026):** `response_mime_type` → polymorphic `response_format`. Routing through **LiteLLM ≥ 1.40** insulates the codebase from this change — do not call Gemini directly.
- **Gemini 2.5+ supports `responseSchema` / JSON Schema structured output.** Use it for PBIR (full JSON). TMDL is whitespace-sensitive text, so use XML-tag extraction (`<tmdl>…</tmdl>`) wrapped in a JSON envelope.
- **PBI-Inspector V2 CLI** is cross-platform (Linux + Windows) and runs headlessly in CI for rules-based report quality checks.
- **Prior art to study (not reinvent):** `lukasreese/powerbi-claude-skills` (PBIR Report Builder), `MinaSaad1/pbi-cli` (TOM + PBIR for AI agents), `NatVanG/PBI-Inspector`.
- **`fabric-cicd`** (out of scope here) is a Python library on PyPI — design does not block it.

---

## System Architecture

```
Browser
  │
  │  HTTPS
  ▼
┌─────────────────────────────────────────────────────────┐
│           Streamlit Frontend  (port 8501)               │
│  1. Connect Source   2. Configure Model                  │
│  3. Describe Report  4. Progress (SSE)                   │
│  5. Download .pbip   6. Chat Refine                      │
└────────────────────┬────────────────────────────────────┘
                     │  REST + SSE
┌────────────────────▼────────────────────────────────────┐
│           FastAPI Backend  (port 8000)                   │
│                                                          │
│  /auth    /connectors    /sessions    /generate          │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │ Connector│  │ Schema   │  │   Generation Pipeline  │  │
│  │  Layer   │→ │ Profiler │→ │  (bg task + SSE)       │  │
│  └──────────┘  └──────────┘  │                        │  │
│                               │  ┌─────────────────┐  │  │
│                               │  │  LLM Router     │  │  │
│                               │  │  (LiteLLM)      │  │  │
│                               │  └─────────────────┘  │  │
│                               │  ┌─────────────────┐  │  │
│                               │  │  TMDL Generator │  │  │
│                               │  └────────┬────────┘  │  │
│                               │           │ validate   │  │
│                               │  ┌────────▼────────┐  │  │
│                               │  │  TE2 CLI (proc) │  │  │
│                               │  └────────┬────────┘  │  │
│                               │           │ retry≤3   │  │
│                               │  ┌────────▼────────┐  │  │
│                               │  │  PBIR Generator │  │  │
│                               │  └────────┬────────┘  │  │
│                               │           │ validate   │  │
│                               │  ┌────────▼────────┐  │  │
│                               │  │ jsonschema +     │  │  │
│                               │  │ cross-ref check  │  │  │
│                               │  └────────┬────────┘  │  │
│                               │           │ retry≤3   │  │
│                               │  ┌────────▼────────┐  │  │
│                               │  │  PBIP Assembler │  │  │
│                               │  └────────┬────────┘  │  │
│                               └───────────┼───────────┘  │
│                                           │               │
│  ┌──────────────────────────────────────── ──────────┐   │
│  │  PostgreSQL (prod) / SQLite (dev)                 │   │
│  │  users · credentials(encrypted) · sessions        │   │
│  │  generation_artifacts (current+history)            │   │
│  └───────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
         │
         │  subprocess / TCP
         ▼
  .NET Runtime + TE2 CLI  (sidecar or same container)
```

**Data flow, "connect source" → valid `.pbip`:**

1. User authenticates → receives JWT
2. User creates a connector (credentials encrypted, stored in DB)
3. Schema Profiler introspects source → returns `SchemaProfile` JSON
4. User describes report → POST `/sessions` → background task starts, `session_id` returned
5. Frontend opens SSE stream `/sessions/{id}/events`
6. Pipeline emits progress events; stages: profile → gen_model → validate_model → gen_report → validate_report → assemble
7. On each validation failure: repair prompt → retry (max 3); if still failing, partial result + user warning
8. Final artifact: `.pbip` folder zipped → stored in session → user downloads
9. User optionally sends refinement chat messages → pipeline surgically patches + revalidates → new download available

---

## PBIP Folder Structure (generated output)

```
{ProjectName}.pbip                          ← project entry point
{ProjectName}/
  {ProjectName}.SemanticModel/
    .platform                               ← item metadata (logicalId, type)
    item.config.json
    definition/
      model.tmdl                            ← top-level model (culture, defaultPowerBIDataSourceVersion)
      tables/
        {TableName}.tmdl                    ← one file per table (columns, measures, M partitions)
      relationships.tmdl
      expressions.tmdl                      ← M expressions / query parameters
  {ProjectName}.Report/
    .platform
    item.config.json
    definition/
      definition.pbir                       ← report entry point + semantic model reference
      report.json                           ← theme, culture, report-level settings
      pages/
        ReportSection_{id}/
          page.json                         ← page name, dimensions (1280×720 default)
          visuals/
            {guid}/
              visualContainer.json          ← one per visual: type, position, query, formatting
```

Key boilerplate files are static (see Assembler component). Only TMDL files and PBIR JSON files are AI-generated.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | **Python 3.12** | TOM ecosystem, fabric-cicd, LiteLLM, pandas all Python-first |
| Backend framework | **FastAPI** | async-native, SSE support, OpenAPI docs, strong typing |
| Frontend | **Streamlit** | Fastest UI for data tools; good session isolation for multi-user |
| ORM | **SQLAlchemy 2.x + Alembic** | Also used as data connector engine for SQL databases |
| LLM routing | **LiteLLM ≥ 1.40** | Unified interface; survives Gemini June 2026 API migration |
| Data validation | **Pydantic v2** | All schema profiles, API contracts, config as typed models |
| PBIR JSON validation | **jsonschema** | Validates against Microsoft's public `$schema` URLs |
| TMDL validation | **TE2 CLI subprocess** (or `pbi-tools`) | No pure-Python alternative exists |
| Report quality checks | **PBI-Inspector V2 CLI** | Cross-platform headless; runs in CI |
| Credential encryption | **cryptography (Fernet)** | Symmetric encryption; server-side key derivation per user |
| Auth | **PyJWT + passlib[bcrypt]** | Simple JWT; no external auth provider needed for self-hosted |
| Package management | **uv** | Fast, lockfile-based |
| Databases | **SQLite** (dev) / **PostgreSQL 16** (prod) | SQLAlchemy handles both transparently |
| Containers | **Docker + docker-compose** | With Caddy sidecar for auto-SSL |
| CI | **GitHub Actions** | pytest + golden file checks + PBI-Inspector |
| Testing | **pytest + pytest-asyncio + vcrpy** | VCR cassettes for LLM calls |
| Data connectors | SQLAlchemy, snowflake-connector-python, google-cloud-bigquery, databricks-sql-connector, pandas/openpyxl | Per-provider SDKs |

---

## Component Design

### 1. Data Connector Layer
**Responsibility:** Establish a tested connection to a data source; do not yet read schema.  
**Interface:**
```python
class DataConnector(Protocol):
    async def test_connection(self) -> ConnectionTestResult: ...
    async def get_engine(self) -> AsyncEngine: ...  # SQLAlchemy engine or wrapper

class SQLConnector:           # PostgreSQL, MySQL, SQL Server via SQLAlchemy
class SnowflakeConnector:     # snowflake-connector-python
class BigQueryConnector:      # google-cloud-bigquery
class DatabricksConnector:    # databricks-sql-connector
class FileConnector:          # CSV / Excel via pandas — no persistent engine
```
**Input:** `ConnectorConfig` Pydantic model (type, host, port, db, user, encrypted_password, etc.)  
**Output:** Testable connection handle / SQLAlchemy engine

---

### 2. Schema Profiler
**Responsibility:** Introspect tables, columns, types, PKs/FKs, cardinality, and sample rows. Apply token-budget truncation for large schemas.

**Interface:**
```python
async def profile_schema(
    connector: DataConnector,
    selected_tables: Optional[List[str]] = None,
    max_tables: int = 40,
    max_columns_per_table: int = 25,
    sample_rows: int = 5,
) -> SchemaProfile: ...
```

**Output model:**
```python
class ColumnProfile(BaseModel):
    name: str
    data_type: str          # normalised: "string"|"integer"|"decimal"|"datetime"|"boolean"|"binary"
    nullable: bool
    is_primary_key: bool
    is_foreign_key: bool
    fk_ref: Optional[str]  # "TableName.ColumnName"
    approx_cardinality: int
    sample_values: List[str]  # ≤5, truncated to 50 chars each

class TableProfile(BaseModel):
    name: str
    schema_name: str
    approx_row_count: int
    columns: List[ColumnProfile]

class SchemaProfile(BaseModel):
    source_type: str
    database: str
    tables: List[TableProfile]
    inferred_relationships: List[RelationshipHint]  # from FK + naming conventions
    profiled_at: datetime
    token_estimate: int          # rough estimate for prompt budgeting
    truncated: bool              # True if tables/columns were dropped
```

**Truncation strategy:** Sort tables by FK reference count (most-connected first). Drop lowest-priority tables if `token_estimate > 7000`. Drop columns beyond `max_columns_per_table` keeping PKs, FKs, and highest-cardinality first.

---

### 3. LLM Router (Multi-Model Abstraction)
**Responsibility:** Provide a single, provider-agnostic interface for all LLM calls. Adding a new model = one entry in `models.yaml`.

**Interface:**
```python
class LLMRouter:
    def __init__(self, model_id: str, api_key: str): ...

    async def complete(
        self,
        messages: List[dict],          # OpenAI-format message list
        temperature: float = 0.1,
        max_tokens: int = 8192,
    ) -> str: ...

    async def complete_json(
        self,
        messages: List[dict],
        json_schema: Optional[dict] = None,  # None = JSON mode; dict = structured output
        max_tokens: int = 8192,
    ) -> dict: ...
```

**Implementation:** Thin wrapper around `litellm.acompletion()`. `model_id` is a LiteLLM-format string:
- `"gemini/gemini-2.5-flash"` (default for generation)
- `"gemini/gemini-2.5-pro"` (higher quality option)
- `"anthropic/claude-opus-4-8"`
- `"openai/gpt-4o"`

**`models.yaml`** (drives the Streamlit model selector dropdown):
```yaml
models:
  - id: gemini/gemini-2.5-flash
    display_name: "Gemini 2.5 Flash"
    provider: google
    env_key: GEMINI_API_KEY
    supports_structured_output: true
    recommended: true
  - id: gemini/gemini-2.5-pro
    display_name: "Gemini 2.5 Pro"
    provider: google
    env_key: GEMINI_API_KEY
    supports_structured_output: true
  - id: anthropic/claude-opus-4-8
    display_name: "Claude Opus 4.8"
    provider: anthropic
    env_key: ANTHROPIC_API_KEY
    supports_structured_output: true
  - id: openai/gpt-4o
    display_name: "GPT-4o"
    provider: openai
    env_key: OPENAI_API_KEY
    supports_structured_output: true
```

User's API key for the selected provider is retrieved from their encrypted DB record and passed at call time — never stored in env vars at the application level.

---

### 4. Generation Engine (Two-Stage Pipeline)

**Stage 1 — Semantic Model (TMDL)**

TMDL is whitespace-sensitive text, not JSON. Structured JSON output mode cannot constrain it directly. Strategy: request a JSON envelope where the values are TMDL strings. The LLM produces valid JSON; the TMDL content is inside string values.

**System prompt:**
```
You are a Power BI semantic model architect. Generate a valid TMDL (Tabular Model Definition Language)
semantic model from the schema context provided.

RULES:
- All table and column names must exactly match the source schema
- Data types: string, int64, double, decimal, dateTime, boolean, binary
- Include all FK relationships from the schema (or inferred from naming)
- Create DAX measures appropriate to the user's reporting request
- Measures use SINGLE QUOTES: [Total Sales] = SUM(Sales[Amount])
- Table files use the keyword "table {TableName}" followed by indented properties
- Do not create calculated columns unless explicitly needed
- M partitions use the source's native query language (SQL for relational sources)

TMDL SYNTAX EXAMPLE:
<example>
table Sales
    partition Sales = m
        mode: Import
        source =
            let
                Source = Sql.Database("server", "db"),
                dbo_Sales = Source{[Schema="dbo",Item="Sales"]}[Data]
            in
                dbo_Sales

    column SalesID
        dataType: int64
        sourceColumn: SalesID
        isHidden

    column Amount
        dataType: decimal
        sourceColumn: Amount
        formatString: "$#,##0.00"

    measure 'Total Sales' = SUM(Sales[Amount])
        formatString: "$#,##0.00"
        displayFolder: "Revenue"
</example>

Return ONLY valid JSON in this exact shape — nothing else:
{
  "model_tmdl": "<full model.tmdl content>",
  "tables": { "Sales.tmdl": "<content>", "Products.tmdl": "<content>" },
  "relationships_tmdl": "<full relationships.tmdl content>",
  "expressions_tmdl": "<full expressions.tmdl content or empty string>"
}
```

**User message:** schema profile JSON (truncated to budget) + user's report request.

**Stage 2 — Report (PBIR)**

PBIR is JSON — use structured output mode.  
**Critical:** Pass the validated TMDL as context so visual query bindings reference only real tables/measures.

**System prompt:**
```
You are a Power BI report designer generating PBIR (Power BI Enhanced Report Format) JSON.

RULES:
- Only reference tables, columns, and measures that exist in the SEMANTIC MODEL below
- Measure references use: {"Measure": {"Expression": {"SourceRef": {"Entity": "TableName"}}, "Property": "MeasureName"}}
- Column references use: {"Column": {"Expression": {"SourceRef": {"Entity": "TableName"}}, "Property": "ColumnName"}}
- Supported visual types: barChart, lineChart, columnChart, pieChart, donutChart,
  tableEx, matrixVisual, card, multiRowCard, slicer, kpiVisual, areaChart, scatterChart
- Maximum 6 visuals per page, maximum 3 pages
- Default page dimensions: width=1280, height=720
- Positions: x/y/width/height in pixels, no overlap, leave 20px margins
- Visual GUIDs must be unique UUID4 strings

SEMANTIC MODEL:
{validated_tmdl_json}

PBIR VISUAL EXAMPLE (barChart):
<example_json>
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.0.0/schema.json",
  "id": "550e8400-e29b-41d4-a716-446655440001",
  "position": {"x": 20, "y": 60, "z": 0, "width": 580, "height": 340, "tabOrder": 1},
  "visual": {
    "visualType": "barChart",
    "query": {
      "queryState": {
        "Category": {"projections": [{"field": {"Column": {"Expression": {"SourceRef": {"Entity": "Products"}}, "Property": "Category"}}, "queryRef": "Products.Category", "active": true}]},
        "Y": {"projections": [{"field": {"Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": "Total Sales"}}, "queryRef": "Sales.Total Sales", "active": true}]}
      }
    },
    "title": {"show": {"expr": {"Literal": {"Value": "true"}}}, "text": {"expr": {"Literal": {"Value": "'Sales by Category'"}}}}
  }
}
</example_json>
```

**Response schema** (passed to `complete_json`):
```python
PBIR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "report_json": {"type": "object"},
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_id": {"type": "string"},
                    "page_json": {"type": "object"},
                    "visuals": {
                        "type": "array",
                        "items": {"type": "object",
                                  "properties": {"visual_id": {"type": "string"}, "visual_json": {"type": "object"}},
                                  "required": ["visual_id", "visual_json"]}
                    }
                },
                "required": ["page_id", "page_json", "visuals"]
            }
        }
    },
    "required": ["report_json", "pages"]
}
```

---

### 5. PBIP Assembler
**Responsibility:** Write the AI-generated artifacts into the correct folder hierarchy.

```python
def assemble_pbip(
    project_name: str,
    semantic_model: SemanticModelArtifacts,
    report: ReportArtifacts,
    connection_config: ConnectorConfig,
    output_dir: Path,
) -> Path:
    """Returns path to {output_dir}/{project_name}/ containing the full .pbip tree."""
```

Static boilerplate files (embedded as Python string constants, not AI-generated):
- `{name}.pbip` — project entry point with version + artifact paths
- `.platform` files — logicalId (generated UUID), type declaration
- `item.config.json` files — displayName, type
- `definition.pbir` — semantic model reference (relative path)

---

### 6. Validation Engine

**PBIR validation (pure Python):**
1. `json.loads()` — syntax check; fail fast on malformed JSON
2. `jsonschema.validate(content, fetch_schema(content["$schema"]))` — validate each generated file against its published schema URL; schemas are fetched once and cached locally in `backend/app/validation/schemas/` at build time (not fetched at runtime to avoid network dependency)
3. **Cross-reference check** — parse all measure/column references in `visualContainer.json` files; assert each referenced Entity + Property exists in the validated TMDL artifact

**TMDL validation (subprocess):**
```python
async def validate_tmdl(tmdl_dir: Path) -> ValidationResult:
    proc = await asyncio.create_subprocess_exec(
        str(TE2_CLI_PATH),     # path to TabularEditor.exe or te2 binary
        str(tmdl_dir),
        "-A",                  # Best Practice Analyzer — exits non-zero on errors
        "--exit-on-error",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        timeout=30,
    )
    stdout, stderr = await proc.communicate()
    return parse_te2_output(proc.returncode, stdout, stderr)
```

*Note: Exact TE2 CLI flags for "load TMDL folder and report errors" must be verified against current TE2 documentation before implementation. `pbi-tools compile` is a fallback if TE2 packaging proves difficult.*

**`ValidationResult` model:**
```python
class ValidationResult(BaseModel):
    valid: bool
    errors: List[ValidationError]   # each has: file, path, message, severity
    warnings: List[ValidationError]
```

---

### 7. Validation + Retry Loop

```python
MAX_RETRIES = 3

async def generate_with_retry(
    stage: Literal["model", "report"],
    base_prompt: GenerationPrompt,
    llm: LLMRouter,
    validator: Validator,
) -> GenerationResult:
    last_result = None
    for attempt in range(MAX_RETRIES + 1):
        if attempt == 0:
            result = await generate(base_prompt, llm)
        else:
            repair_prompt = build_repair_prompt(last_result, last_validation)
            result = await generate(repair_prompt, llm)

        validation = await validator.validate(result)
        if validation.valid:
            return GenerationResult(artifacts=result, attempts=attempt + 1)
        last_result, last_validation = result, validation

    # All retries exhausted
    return GenerationResult(artifacts=last_result, valid=False,
                            errors=last_validation.errors, partial=True)
```

**Repair prompt template:**
```
The {stage} you generated has the following validation errors:

ERRORS:
{error_list}

CURRENT CONTENT:
{current_content}

Fix ONLY these errors. Do not change anything that is not listed above.
Return the corrected content in the same JSON format as before.
```

Progress events emitted during retry: `{type: "retry", attempt: 2, stage: "report", errors: [...]}`

---

### 8. Generation Session + State Store

**DB model (`generation_sessions` table):**
```python
class GenerationSession(Base):
    id: UUID (PK)
    user_id: UUID (FK)
    connector_id: UUID (FK)
    status: str          # "running"|"complete"|"failed"|"refining"
    original_request: str
    schema_profile: JSON
    conversation_history: JSON   # List of {role, content} dicts (last 10 turns)
    current_artifacts: JSON      # All TMDL + PBIR file content (current state)
    artifact_history: JSON       # List of artifact snapshots (for undo, max 5)
    download_path: str           # Path to latest assembled .zip
    created_at: datetime
    updated_at: datetime
```

---

### 9. AI Chat Refinement Loop

This is the most technically complex component. Approach for v1: **targeted file regeneration** (not surgical diff-patching, which is a v2 optimization).

**Edit classification (fast call using a cheap model, e.g. Flash):**
```python
CLASSIFY_PROMPT = """
Classify this report edit request. Return JSON: {"scope": [...]}
Valid scope values: "TMDL_NEW_MEASURE", "TMDL_MODIFY_MEASURE", "TMDL_STRUCTURE",
                   "PBIR_VISUAL_TYPE", "PBIR_VISUAL_QUERY", "PBIR_FORMATTING",
                   "PBIR_NEW_VISUAL", "PBIR_NEW_PAGE", "FULL_REGENERATION"

Edit request: "{edit}"
"""
```

**Refinement pipeline:**
1. Classify edit → determine scope set
2. Build context: relevant TMDL sections + affected visual(s) from current artifacts
3. Construct targeted prompt: "Here is the current {file}. Apply this change: {edit}. Return the updated file."
4. Regenerate only affected files (if scope is "PBIR_VISUAL_TYPE" for one visual, regenerate only that `visualContainer.json`)
5. Apply new file(s) to current artifact set (replacing old versions)
6. Validate the changed files only
7. Reassemble full .pbip; store snapshot in `artifact_history` before replacing `current_artifacts`
8. Emit "complete" event with new download URL

**Cross-layer edit example** ("make the revenue chart a line chart and add a 12-month rolling average"):
- Scope: `["TMDL_NEW_MEASURE", "PBIR_VISUAL_TYPE", "PBIR_VISUAL_QUERY"]`
- Step 1: Generate new DAX measure in TMDL (`Rolling 12M Revenue`)
- Step 2: Regenerate the target visual's `visualContainer.json` with `visualType: "lineChart"` + new measure in query
- Step 3: Validate TMDL + visual file; retry if needed
- Step 4: Reassemble

**Visual identification:** When the user refers to "the revenue chart", use the conversation history + a search over visual titles and measure references in current PBIR artifacts to identify the target visual ID.

---

## Auth + Multi-User Design

**Endpoints:**
```
POST /auth/register      → {email, password} → 201, user_id
POST /auth/login         → {email, password} → {access_token, refresh_token}
POST /auth/refresh       → {refresh_token}   → {access_token}
GET  /auth/me            → user profile
```

**Credential storage:**
```python
class StoredCredential(Base):
    id: UUID; user_id: UUID; name: str; connector_type: str
    encrypted_json: bytes    # Fernet(derive_key(master_key, user_id)).encrypt(json)
```

Master key from `ENCRYPTION_KEY` env var. Per-user key derivation via PBKDF2-HMAC (user_id as salt). Credentials never logged, never returned to client — only decrypted server-side at connection time.

**Streamlit auth pattern:** JWT stored in `st.session_state["token"]`. Every Streamlit page checks token validity on load; expired token redirects to login. Backend validates Bearer token on every API call.

---

## Project Folder Structure

```
automation-tool-bi/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app, lifespan, CORS
│   │   ├── config.py                # pydantic-settings (env vars)
│   │   ├── models/                  # SQLAlchemy ORM models
│   │   │   ├── user.py
│   │   │   ├── credential.py
│   │   │   └── session.py
│   │   ├── schemas/                 # Pydantic API request/response models
│   │   ├── api/
│   │   │   ├── auth.py
│   │   │   ├── connectors.py
│   │   │   ├── sessions.py          # generate, stream, download, refine
│   │   │   └── models.py            # list available LLM models
│   │   ├── connectors/
│   │   │   ├── base.py
│   │   │   ├── sql.py               # SQLAlchemy (PG, MySQL, SQL Server)
│   │   │   ├── snowflake.py
│   │   │   ├── bigquery.py
│   │   │   ├── databricks.py
│   │   │   └── files.py             # CSV / Excel
│   │   ├── profiler/
│   │   │   └── profiler.py
│   │   ├── llm/
│   │   │   ├── router.py            # LiteLLM wrapper
│   │   │   └── models.yaml
│   │   ├── generation/
│   │   │   ├── pipeline.py          # orchestrates stages + retry
│   │   │   ├── prompts/
│   │   │   │   ├── tmdl_system.txt
│   │   │   │   ├── tmdl_repair.txt
│   │   │   │   ├── pbir_system.txt
│   │   │   │   └── pbir_repair.txt
│   │   │   ├── semantic_model.py    # Stage 1: TMDL generation
│   │   │   └── report.py            # Stage 2: PBIR generation
│   │   ├── assembler/
│   │   │   ├── assembler.py
│   │   │   └── templates/           # static boilerplate file content
│   │   ├── validation/
│   │   │   ├── pbir_validator.py    # jsonschema + cross-ref
│   │   │   ├── tmdl_validator.py    # TE2 CLI subprocess
│   │   │   └── schemas/             # cached PBIR JSON schemas (fetched at build)
│   │   └── refinement/
│   │       ├── refiner.py
│   │       └── classifier.py
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── golden/
│   │       └── fixtures/            # reference .pbip trees for golden tests
│   ├── alembic/
│   ├── pyproject.toml               # uv-managed
│   └── Dockerfile
├── frontend/
│   ├── app.py                       # Streamlit entry + nav
│   ├── pages/
│   │   ├── 1_connect.py
│   │   ├── 2_generate.py
│   │   └── 3_refine.py
│   ├── components/
│   │   ├── auth.py                  # login/register forms
│   │   ├── connector_form.py
│   │   └── progress_stream.py       # SSE consumer
│   └── requirements.txt
├── tools/                           # bundled CLI tools
│   └── te2/                         # Tabular Editor 2 binary + .NET runtime
├── docker-compose.yml               # prod: backend + frontend + postgres + caddy
├── docker-compose.dev.yml           # dev: adds pgAdmin, auto-reload
├── Caddyfile                        # auto-SSL for self-hosted
└── .github/workflows/
    ├── ci.yml                       # lint + unit + integration + golden tests
    └── build.yml
```

---

## Milestone Plan (6–8 weeks, 3–4 engineers)

### Week 1–2: Foundation + Single-Connector Proof of Concept
**All engineers:**
- A: Project scaffold (uv, Docker, docker-compose, Alembic, GitHub Actions CI skeleton)
- A: Auth (JWT endpoints, user model, bcrypt passwords)
- B: PostgreSQL connector + schema profiler (full introspection, type normalisation, FK detection)
- C: LiteLLM router + TMDL generation Stage 1 with Gemini (hand-craft 3 prompt iterations)
- D: PBIP assembler (static boilerplate + file-write logic)

**Checkpoint:** `POST /generate` with a Postgres connection + "show me sales by product" → downloads a syntactically plausible `.pbip` folder (not yet validated). Open in Power BI Desktop manually to check.

---

### Week 3: Full Pipeline + Validation + Retry
- PBIR Stage 2 generation (Gemini)
- PBIR JSON schema validation (jsonschema + cached schemas)
- Cross-reference validation (measures referenced in visuals exist in TMDL)
- TMDL validation via TE2 CLI (subprocess; bundle TE2 in Docker)
- Retry loop (max 3 attempts, repair prompt)
- SSE progress streaming
- Basic Streamlit UI: login → connect → describe → generate → watch progress → download

**Checkpoint:** End-to-end pipeline runs reliably for 3 different Postgres schemas. All generated `.pbip` files pass validation. At least 1 opens correctly in Power BI Desktop. First-pass success rate measured (target: >90% after retry).

---

### Week 4: MySQL + SQL Server + Error Hardening
- MySQL connector (SQLAlchemy dialect)
- SQL Server connector (pyodbc or aioodbc + SQLAlchemy)
- Structured error handling: validation failure messages shown in Streamlit with user-friendly explanation
- Rate limiting (slowapi) + input validation
- Unit test suite for connectors + profiler + assembler + validator

**Checkpoint:** Same pipeline works for all 3 SQL connectors. CI passing.

---

### Week 5: Cloud Connectors + Multi-Model Router
- Snowflake, BigQuery, Databricks connectors
- CSV / Excel file connector
- Connector credential UI (create, test, list, delete)
- LiteLLM router fully wired: user selects model in UI, API key stored encrypted
- Verify pipeline works with Claude (if API key available) and GPT-4o

**Checkpoint:** All 7 connector types work. User can switch AI model. Golden file tests in CI covering 5+ generation scenarios.

---

### Week 6: Refinement Loop (Phase 2)
- `GenerationSession` DB model + artifact snapshot logic
- Edit classifier (fast Flash call)
- Targeted file regeneration for common edit types
- Refinement chat UI in Streamlit
- Undo (revert to previous snapshot)
- Handle cross-layer edits (TMDL + PBIR in same request)

**Checkpoint:** User can take a generated report and successfully apply 3 natural-language edits (change chart type, add a measure, add a page). Each edit re-validates cleanly.

---

### Weeks 7–8: Hardening + Deployment
- Performance: async connection pooling, parallel visual generation (generate all page visuals concurrently within Stage 2)
- PBI-Inspector V2 integration as optional CI quality gate
- LLM-as-judge quality scoring (logged, non-blocking)
- docker-compose production config with Caddy (auto-SSL)
- Secrets management documentation (`.env.example`, deployment guide)
- Integration test suite with VCR cassettes (no live LLM calls in CI)
- Weekly manual smoke test protocol documented

**Checkpoint:** Deployed to a staging server. 5+ scenarios tested end-to-end. All CI checks green. Known limitations documented.

---

## Testing Strategy

### Unit tests (no external calls)
- `test_profiler.py`: mock DB cursor returns → assert `SchemaProfile` fields, truncation behaviour
- `test_assembler.py`: given `SemanticModelArtifacts` + `ReportArtifacts` → assert correct file tree exists, correct boilerplate content
- `test_pbir_validator.py`: known-valid and known-invalid `visualContainer.json` fixtures → assert pass/fail
- `test_tmdl_validator.py`: mock subprocess → assert correct error parsing
- `test_llm_router.py`: mock `litellm.acompletion` → assert correct message construction and JSON parsing
- `test_auth.py`: JWT encoding/expiry, password hashing

### Integration tests (live DB, recorded LLM)
- Docker Compose with PostgreSQL + MySQL + SQL Server test containers
- VCR.py cassettes for all LLM calls (record once with real Gemini key, replay in CI)
- Full pipeline: `profile_schema → generate_model → validate_model → generate_report → validate_report → assemble`
- Assert: all PBIR files parse against their `$schema`
- Assert: TMDL compiles without errors via TE2 CLI
- Assert: cross-references are consistent (all visual measure refs exist in TMDL)

### Golden file tests (regression)
- Maintain `tests/golden/fixtures/` with 8–10 reference scenarios (e.g., "ecommerce sales schema, request: sales dashboard with KPIs")
- On each commit: regenerate from fixed schema + request using VCR cassette; compare generated file tree structure against golden
- Do NOT assert identical content (LLM output varies) — assert structure, schema validity, and cross-reference consistency

### Quality evaluation (non-blocking monitoring)
- After each generation, run a secondary LLM call: "Given this schema and request, score this report definition 1–5 for relevance, completeness, and visual appropriateness. JSON: {score, reasoning}"
- Log to `quality_evaluations` table
- Alert if rolling 7-day average drops below 3.5/5
- Manual weekly check: open 3 generated `.pbip` files in Power BI Desktop; log visual render status in a shared doc

### What cannot be automated
- **Visual rendering correctness** requires Power BI Desktop (no headless renderer). Covered by manual weekly smoke test.
- **Data accuracy** (does the DAX actually produce correct numbers) requires a live data source. Covered by integration tests against test containers with known data.

---

## Key Risks + Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **TMDL validation without .NET** — no Python parser exists | HIGH | Bundle TE2 CLI in Docker image (`mcr.microsoft.com/dotnet/runtime:8.0` base layer + TE2 binary). Verify TE2 licensing for this use (open-source TE2 is free). `pbi-tools compile` is the fallback. Must verify exact CLI flags before Week 3. |
| **PBIR visual type property gaps** — schema documents structure but not all valid values for every visual type | HIGH | Build a reference visual library: export 10 real visuals from Power BI Desktop and embed as few-shot examples in prompts. Start with 8 visual types. Expand library as issues surface. |
| **Gemini API June 2026 migration** — `response_mime_type` removed | MEDIUM | LiteLLM ≥ 1.40 handles the new `response_format` field. Pin LiteLLM version; test on Gemini 2.5 Flash weekly until migration stabilises. |
| **Refinement loop surgical patching complexity** | HIGH | Ship v1 as "targeted file regeneration" (regenerate only affected files, not line-level diffs). Defer surgical patching to v2. If refinement is behind in Week 6, fall back to full report regeneration with conversation history. |
| **Power BI Desktop PBIR preview mode** | MEDIUM | Document clearly: users must have PBI Desktop ≥ March 2026 with PBIR preview feature enabled. Add a setup guide to the UI onboarding screen. |
| **TE2 Docker image size** | LOW | TE2 + .NET 8 Runtime adds ~200–300MB. Acceptable for a self-hosted service. Use multi-stage build to keep production image lean. |
| **Timeline: refinement loop slips** | MEDIUM | Weeks 1–5 are the minimum viable deliverable (full generation pipeline with all connectors). Refinement loop (Week 6) can be shipped as "beta" in a follow-on week. |
| **Encrypted credential security** | MEDIUM | Use `cryptography.fernet` with a server-side `ENCRYPTION_KEY` in env. Document that the key must be stored in a secrets manager (e.g. AWS Secrets Manager, Azure Key Vault) in production — not in a `.env` file. Rotate key procedure must be documented before v1 ships. |

### Hardest parts (honest call-out)

1. **Getting TMDL M partitions right.** M query generation for each connector type (Snowflake's `Snowflake.Databases()`, BigQuery's `GoogleBigQuery.Database()`, etc.) has connector-specific syntax. Build a lookup table of M source templates per connector type — do not rely on the LLM to know these accurately.

2. **Visual query binding syntax in PBIR.** The `queryState` field in `visualContainer.json` has a complex, nested structure that varies by field role (Category, Y, Tooltip, etc.) and visual type. The few-shot examples in prompts are critical — without them the LLM generates plausible-looking but invalid query bindings. Verify against `visualContainer/2.0.0/schema.json` before finalising example templates.

3. **Cross-layer refinement edits.** "Add a 12-month rolling average" requires a new DAX measure in TMDL AND a new query binding in the visual. Both must be regenerated atomically and re-validated together. The edit classifier must detect this scope; the refiner must handle the two-stage update.

### What to verify against current Microsoft documentation before starting

- Exact PBIR `$schema` version strings for all file types (report.json, page.json, visualContainer.json) — versions may have incremented
- TE2 CLI exact command for "load TMDL folder, run BPA, report errors, exit non-zero on failure" — verify against Tabular Editor 2 docs
- M query source function signatures for each cloud connector (Snowflake, BigQuery, Databricks) — these change with Power Query Online updates
- Whether Gemini 2.5 Flash `responseSchema` mode is stable after the June 2026 API migration — test with a simple schema before committing to it for PBIR generation

---

## Verification Plan (end-to-end test of the delivered system)

1. Start stack: `docker compose up`
2. Register a user, log in
3. Create a PostgreSQL connector (point to test DB with `Northwind` or similar schema)
4. Profile schema — assert 10+ tables appear in the UI
5. Select "Gemini 2.5 Flash", enter "Create a sales dashboard with total revenue, top 10 products, and monthly trend"
6. Watch SSE progress; confirm no retry events on first attempt (if retry occurs, record count)
7. Download `.pbip` zip, unzip
8. Assert: `jsonschema` validation passes on all PBIR files (automated script in `tools/validate_pbip.py`)
9. Assert: TE2 CLI compiles TMDL without errors
10. Open in Power BI Desktop (March 2026+, PBIR preview enabled) — assert no error dialogs, visuals render
11. Return to tool, type "change the monthly trend to a line chart" — assert refined report downloads
12. Open refined report — assert the changed visual is now a line chart
