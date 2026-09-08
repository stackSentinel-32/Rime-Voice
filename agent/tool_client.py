"""Tool clients used by the orchestrator.

`LocalToolClient` calls backend.tools in-process (hermetic test + reference impl).
`HttpToolClient` talks to the FastAPI backend over HTTP (full app / Docker).

Both honor an injected `delay_ms` so the race condition is reproducible (roadmap §5.3),
and both return the dispatch `turn_version` alongside the payload so the orchestrator can
check staleness on arrival.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from backend.tools import (
    commit_booking,
    lookup_booking,
    validate_cancel,
    validate_update,
)


class LocalToolClient:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def run(self, tool_name: str, args: dict, turn_version: int) -> dict:
        delay_ms = int(args.get("delay_ms", 0) or 0)
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)
        if tool_name == "lookup_booking":
            with self._sf() as s:
                return {"turn_version": turn_version, "booking": lookup_booking(s, args["booking_id"])}
        if tool_name == "update_booking":
            with self._sf() as s:
                return {"turn_version": turn_version,
                        "proposal": validate_update(s, args["booking_id"], args.get("changes", {}))}
        if tool_name == "cancel_booking":
            with self._sf() as s:
                return {"turn_version": turn_version, "proposal": validate_cancel(s, args["booking_id"])}
        raise ValueError(f"unknown tool: {tool_name}")

    async def commit(self, proposal: dict) -> dict:
        with self._sf() as s:
            return commit_booking(s, proposal)


class HttpToolClient:
    """Talks to backend/main.py. Requires httpx (see backend/requirements.txt)."""

    def __init__(self, base_url: str, client=None):
        self.base_url = base_url.rstrip("/")
        self._client = client  # optional shared httpx.AsyncClient

    async def _post(self, path: str, json: dict) -> dict:
        import httpx

        if self._client is not None:
            r = await self._client.post(self.base_url + path, json=json)
        else:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(self.base_url + path, json=json)
        r.raise_for_status()
        return r.json()

    async def run(self, tool_name: str, args: dict, turn_version: int) -> dict:
        body = {"turn_version": turn_version, **{k: v for k, v in args.items()}}
        return await self._post(f"/tools/{tool_name}", body)

    async def commit(self, proposal: dict) -> dict:
        return await self._post("/commit", {"proposal": proposal})
