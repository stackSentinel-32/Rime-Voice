"""FastAPI HTTP wrapper over backend.tools + demo/evidence/token endpoints.

Static UI (web/) is mounted last so API routes keep precedence.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import tools as T
from .db import Base, make_engine, make_session_factory
from .schemas import CancelReq, CommitReq, LookupReq, UpdateReq
from .seed import seed

app = FastAPI(title="Rime Voice Booking Backend")

_engine = make_engine()
Base.metadata.create_all(_engine)
_SF = make_session_factory(_engine)
seed(_SF, n=40, reset=False)  # synthetic data: seed if empty (SQLite/Render boot)


def _default_delay() -> int:
    try:
        return int(os.getenv("TOOL_CALL_DELAY_MS", "0"))
    except ValueError:
        return 0


# ---- infra / status -------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status():
    """Which speech providers are active — observable in the running app."""
    return {
        "stt_provider": os.getenv("STT_PROVIDER", "deepgram"),
        "tts_provider": "rime",
        "rime_model": os.getenv("RIME_MODEL", "unset"),
        "rime_speaker": os.getenv("RIME_SPEAKER", "unset"),
        "rime_lang": os.getenv("RIME_LANG", "unset"),
        "llm_provider": "google-gemini",
        "llm_model": os.getenv("GEMINI_MODEL", "unset"),
        "tool_call_delay_ms": _default_delay(),
    }


# ---- LiveKit token --------------------------------------------------------

@app.get("/token")
def token(room: str = Query("demo"), identity: str = Query("judge")):
    key, secret = os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")
    if not key or not secret:
        raise HTTPException(503, "LIVEKIT_API_KEY / LIVEKIT_API_SECRET not configured")
    try:
        from livekit import api
    except ImportError:
        raise HTTPException(503, "livekit-api not installed on this deployment")
    t = api.AccessToken(key, secret).with_identity(identity).with_name(identity)
    t.with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
    return {"token": t.to_jwt(), "room": room, "identity": identity}


# ---- booking tools --------------------------------------------------------

@app.get("/bookings/{booking_id}")
def get_booking(booking_id: int):
    with _SF() as s:
        try:
            return T.lookup_booking(s, booking_id)
        except T.BookingNotFound:
            raise HTTPException(404, "booking not found")


@app.post("/tools/lookup_booking")
async def lookup(req: LookupReq):
    delay = req.delay_ms if req.delay_ms is not None else _default_delay()
    if delay > 0:
        await asyncio.sleep(delay / 1000)
    with _SF() as s:
        try:
            return {"turn_version": req.turn_version, "booking": T.lookup_booking(s, req.booking_id)}
        except T.BookingNotFound:
            raise HTTPException(404, "booking not found")


@app.post("/tools/update_booking")
async def update(req: UpdateReq):
    if req.delay_ms and req.delay_ms > 0:
        await asyncio.sleep(req.delay_ms / 1000)
    with _SF() as s:
        try:
            return {"turn_version": req.turn_version,
                    "proposal": T.validate_update(s, req.booking_id, req.changes)}
        except T.BookingNotFound:
            raise HTTPException(404, "booking not found")


@app.post("/tools/cancel_booking")
async def cancel(req: CancelReq):
    if req.delay_ms and req.delay_ms > 0:
        await asyncio.sleep(req.delay_ms / 1000)
    with _SF() as s:
        try:
            return {"turn_version": req.turn_version, "proposal": T.validate_cancel(s, req.booking_id)}
        except T.BookingNotFound:
            raise HTTPException(404, "booking not found")


@app.post("/commit")
def commit(req: CommitReq):
    with _SF() as s:
        try:
            return T.commit_booking(s, req.proposal)
        except T.BookingNotFound:
            raise HTTPException(404, "booking not found")


# ---- evidence demo (dashboard backend; zero API keys required) ------------

class DemoRunReq(BaseModel):
    mode: str = "fast"          # "fast" (800/300ms) or "exact" (roadmap 4000/1500ms)
    booking_id: int = 12
    corrected_date: str = "2026-09-18"


@app.post("/demo/run")
async def demo_run(req: DemoRunReq = Body(default=DemoRunReq())):
    """Replay the acceptance scenario against the real orchestrator + this DB."""
    from agent.demo_scenario import MODES, run_interruption_scenario
    from redis_state.session_store import make_redis_client

    if req.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {sorted(MODES)}")
    p = MODES[req.mode]
    threshold = int(os.getenv("CANCEL_LATENCY_THRESHOLD_MS", "300"))
    out = await run_interruption_scenario(
        _SF, make_redis_client(), f"demo-{req.mode}", p["delay_ms"], p["interrupt_at_ms"],
        threshold_ms=threshold, booking_id=req.booking_id, corrected_date=req.corrected_date,
    )
    out["mode"] = req.mode
    out["params"] = p
    return out


# ---- static UI (mounted last: API routes above keep precedence) -----------

_web_dir = Path(__file__).resolve().parent.parent / "web"
if _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")
