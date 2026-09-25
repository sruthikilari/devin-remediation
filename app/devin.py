"""Minimal Devin v3 API calls: create a session and read one.

Any failure raises an ``httpx`` error (or ``ValueError`` for a body that is not JSON).
There is no retry: a failed dispatch fails the run, and a failed poll is tried again
on the next pass.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx


class DevinClient:
    def __init__(self, api_key: str, org_id: str, *, base_url: str = "https://api.devin.ai",
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self._sessions = f"/v3/organizations/{org_id}/sessions"
        self._http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"},
                                  timeout=30.0, transport=transport)

    def create_session(self, prompt: str, **options: Any) -> Dict[str, Any]:
        """Options are Devin's own fields, for example title, repos, max_acu_limit,
        structured_output_schema and structured_output_required. Unset ones are left out."""
        body = {"prompt": prompt, **{k: v for k, v in options.items() if v is not None}}
        return self._call("POST", self._sessions, body)

    def get_session(self, session_id: str) -> Dict[str, Any]:
        return self._call("GET", f"{self._sessions}/{quote(session_id, safe='')}")

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self._http.request(method, path, json=body)
        response.raise_for_status()
        return response.json()
