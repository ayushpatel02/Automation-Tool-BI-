"""Error-diagnosis endpoint: paste an error, get an AI-generated explanation and fix.

Deliberately independent of the generation pipeline's model choice — the user can pick a
cheaper/lower-tier model here to save tokens, since diagnosing an error is usually a much
simpler task than generating a full report.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.llm.router import LLMError, LLMRouter
from app.models import User
from app.schemas.diagnostics import DiagnoseRequest, DiagnoseResponse
from app.services.keys import resolve_api_key

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

_SYSTEM_PROMPT = """You are a troubleshooting assistant for an AI-powered Power BI report \
generator. Users may paste errors from this tool's generation/validation pipeline, or from \
Power BI Desktop, TMDL, PBIR, DAX, or Power Query (M).

Given an error message (and optional context), respond with:
1. **Likely cause** — a short, plain-language explanation.
2. **How to fix it** — concrete, actionable steps. Include corrected code/syntax if relevant.

Be concise. Use markdown. If the error text is too vague to diagnose, say what additional \
information would help."""


@router.post("", response_model=DiagnoseResponse)
async def diagnose_error(
    body: DiagnoseRequest, user: User = Depends(get_current_user)
) -> DiagnoseResponse:
    api_key = resolve_api_key(user, body.model_id)
    llm = LLMRouter(body.model_id, api_key)

    parts = [f"ERROR:\n{body.error_text}"]
    if body.context:
        parts.append(f"\nCONTEXT:\n{body.context}")
    parts.append("\nWhat caused this and how do I fix it?")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]
    try:
        answer = await llm.complete(messages, max_tokens=2048)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"AI request failed: {exc}") from exc

    return DiagnoseResponse(answer=answer, model_id=body.model_id)
