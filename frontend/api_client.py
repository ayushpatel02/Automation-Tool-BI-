"""Thin HTTP client wrapping the FastAPI backend, with JWT handling."""

from __future__ import annotations

import json
import os
from typing import Any, Iterator

import httpx

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")


class ApiError(Exception):
    pass


class ApiClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # --- auth ---
    def register(self, email: str, password: str) -> None:
        self._post("/auth/register", {"email": email, "password": password}, auth=False)

    def login(self, email: str, password: str) -> str:
        data = self._post("/auth/login", {"email": email, "password": password}, auth=False)
        self.token = data["access_token"]
        return self.token

    # --- models ---
    def list_models(self) -> list[dict]:
        return self._get("/models")

    # --- per-user API keys ---
    def list_api_keys(self) -> list[dict]:
        return self._get("/auth/api-keys")

    def set_api_key(self, provider: str, api_key: str) -> list[dict]:
        return self._request(
            "PUT", "/auth/api-keys", {"provider": provider, "api_key": api_key}
        )

    def delete_api_key(self, provider: str) -> list[dict]:
        return self._request("DELETE", f"/auth/api-keys/{provider}")

    # --- connectors ---
    def test_connector(self, config: dict) -> dict:
        return self._post("/connectors/test", config)

    def save_connector(self, config: dict) -> dict:
        return self._post("/connectors", config)

    def list_connectors(self) -> list[dict]:
        return self._get("/connectors")

    def upload_file(self, name: str, content: bytes, mime: str) -> dict:
        """Upload a CSV/Excel file; returns {file_path, connector_type, ...}."""
        try:
            resp = httpx.post(
                f"{BACKEND_URL}/connectors/upload",
                headers=self._headers(),
                files={"file": (name, content, mime)},
                timeout=120,
            )
        except httpx.RequestError as exc:
            raise ApiError(f"Cannot reach backend at {BACKEND_URL}: {exc}") from exc
        if resp.status_code >= 400:
            detail = resp.json().get("detail", resp.text) if resp.content else resp.text
            raise ApiError(f"{resp.status_code}: {detail}")
        return resp.json()

    # --- sessions ---
    def create_session(self, body: dict) -> dict:
        return self._post("/sessions", body)

    def get_session(self, session_id: str) -> dict:
        return self._get(f"/sessions/{session_id}")

    def get_validation(self, session_id: str) -> dict:
        return self._get(f"/sessions/{session_id}/validation")

    def refine(self, session_id: str, message: str) -> dict:
        return self._post(f"/sessions/{session_id}/refine", {"message": message})

    def get_history(self, session_id: str) -> dict:
        return self._get(f"/sessions/{session_id}/history")

    def revert(self, session_id: str) -> dict:
        return self._request("POST", f"/sessions/{session_id}/revert")

    def get_preview(self, session_id: str) -> dict:
        return self._get(f"/sessions/{session_id}/preview")

    def download_bytes(self, session_id: str) -> bytes:
        resp = httpx.get(
            f"{BACKEND_URL}/sessions/{session_id}/download",
            headers=self._headers(),
            timeout=60,
        )
        resp.raise_for_status()
        return resp.content

    def stream_events(self, session_id: str) -> Iterator[dict]:
        """Yield SSE progress events until the stream closes."""
        url = f"{BACKEND_URL}/sessions/{session_id}/events"
        with httpx.stream("GET", url, headers=self._headers(), timeout=None) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    payload = line[len("data:") :].strip()
                    if payload and payload != "{}":
                        try:
                            yield json.loads(payload)
                        except json.JSONDecodeError:
                            continue

    # --- low-level ---
    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, body: dict, auth: bool = True) -> Any:
        return self._request("POST", path, body, auth)

    def _request(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> Any:
        headers = self._headers() if auth else {}
        try:
            resp = httpx.request(
                method, f"{BACKEND_URL}{path}", json=body, headers=headers, timeout=120
            )
        except httpx.RequestError as exc:
            raise ApiError(f"Cannot reach backend at {BACKEND_URL}: {exc}") from exc
        if resp.status_code >= 400:
            detail = resp.json().get("detail", resp.text) if resp.content else resp.text
            raise ApiError(f"{resp.status_code}: {detail}")
        return resp.json() if resp.content else None
