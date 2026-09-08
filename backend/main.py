"""FastAPI HTTP wrapper over backend.tools + demo/evidence/token endpoints.

Static UI (web/) is mounted last so API routes keep precedence.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket
from pydantic import BaseModel

from . import tools as T
from .db import Base, make_engine, make_session_factory
from .schemas import CancelReq, CommitReq, CreateReq, FindReq, LookupReq, UpdateReq
from .seed import seed

app = FastAPI(title="Rime Voice Booking Backend")

_engine = make_engine()
from .db import ensure_schema  # noqa: E402
ensure_schema(_engine)         # add missing columns (e.g. phone) on existing DBs
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
        "llm_provider": "groq",
        "llm_model": os.getenv("GROQ_MODEL", "unset"),
        "tool_call_delay_ms": _default_delay(),
    }


# ---- live voice call (no LiveKit: browser PCM <-> this server over one WS) ----

@app.websocket("/ws/call")
async def ws_call(websocket: WebSocket):
    await websocket.accept()
    from .voice import VoiceCallSession

    session = VoiceCallSession(
        send=lambda msg: websocket.send_json(msg),
        send_audio=lambda pcm: websocket.send_bytes(pcm),
        session_factory=_SF,
    )
    try:
        await session.start()
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            text = msg.get("text")
            if data is not None:
                await session.feed_audio(data)
            elif text is not None:
                # type-to-speak / smoke-test path: treated as a final transcript
                await session.feed_text(text)
    except Exception:
        pass
    finally:
        await session.close()


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


@app.post("/tools/find_bookings")
async def find(req: FindReq):
    if req.delay_ms and req.delay_ms > 0:
        await asyncio.sleep(req.delay_ms / 1000)
    with _SF() as s:
        return {"turn_version": req.turn_version,
                "bookings": T.find_bookings(s, req.customer_name, req.phone)}


@app.post("/tools/create_booking")
async def create(req: CreateReq):
    if req.delay_ms and req.delay_ms > 0:
        await asyncio.sleep(req.delay_ms / 1000)
    with _SF() as s:
        return {"turn_version": req.turn_version,
                "proposal": T.validate_create(s, req.customer_name, req.date, req.time,
                                              req.service_type, req.phone)}


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
