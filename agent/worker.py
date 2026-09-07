"""LiveKit Agents worker entrypoint (roadmap Phase 1-2 skeleton, wired to Phase 3 core).

This is the live-audio composition root. It joins a LiveKit room, streams Rime TTS, feeds
Deepgram partial transcripts into the InterruptionDetector, and routes LLM tool decisions
through the TurnManager (which owns turn-versioning + staleness). The heavy lifting lives
in the transport-agnostic modules; this file just binds them to LiveKit callbacks.

Requires livekit-agents + plugins (see requirements.txt). Import of livekit is deferred so
the rest of the package (and the hermetic test suite) works without those heavy deps.
"""
from __future__ import annotations

import os
import uuid

from agent.event_log import EventLogger
from agent.llm_tools import decide_tool, make_llm
from agent.stt_pipeline import InterruptionDetector, stt_config
from agent.tool_client import HttpToolClient, LocalToolClient
from agent.tts_rime import RimeTTS
from agent.turn_manager import TurnManager
from redis_state.session_store import SessionStore, make_redis_client


def build_orchestrator(session_id: str, livekit_session=None, session_factory=None):
    """Compose the turn-versioning stack for one call. Returns (tm, detector, tts, events)."""
    events = EventLogger(session_id)
    tts = RimeTTS(session=livekit_session, event_logger=events)
    store = SessionStore(make_redis_client(), session_id)

    backend_url = os.getenv("BACKEND_URL")
    if backend_url:
        tools = HttpToolClient(backend_url)
    else:
        # in-process fallback (requires a session_factory)
        tools = LocalToolClient(session_factory)

    def render(tool_name, payload):
        if tool_name == "lookup_booking":
            return (f"Booking {payload['id']} for {payload['customer_name']} is "
                    f"{payload['status']} on {payload['date']} at {payload['time']}.")
        return f"Done. Booking {payload['id']} is now on {payload['date']} ({payload['status']})."

    tm = TurnManager(session_id, store, events, tts, tools, render=render)
    detector = InterruptionDetector(tm)
    return tm, detector, tts, events


async def entrypoint(ctx):
    """LiveKit Agents entrypoint. `ctx` is a JobContext."""
    from livekit.agents import AgentSession  # deferred heavy import

    session_id = getattr(getattr(ctx, "room", None), "name", None) or uuid.uuid4().hex[:8]
    llm = make_llm()
    print(f"[worker] session={session_id} stt={stt_config()['provider']} "
          f"rime_model={os.getenv('RIME_MODEL', 'unset')}")

    livekit_session = AgentSession(**_livekit_components())
    tm, detector, tts, events = build_orchestrator(session_id, livekit_session=livekit_session)

    @livekit_session.on("user_input_transcribed")
    def _on_stt(ev):
        fired = detector.on_user_speech(getattr(ev, "transcript", ""), getattr(ev, "is_final", False))
        if not fired and getattr(ev, "is_final", False):
            decision = decide_tool(llm, ev.transcript)
            if decision:
                detector.set_agent_busy(True)
                import asyncio
                asyncio.create_task(tm.dispatch_tool(decision["tool"], decision["args"]))

    await livekit_session.start(room=ctx.room)


def _livekit_components() -> dict:
    """Build the STT/LLM/TTS plugin instances from env. Imported lazily by entrypoint."""
    from livekit.plugins import deepgram, openai, rime, silero

    return {
        "vad": silero.VAD.load(),
        "stt": deepgram.STT(model=os.getenv("DEEPGRAM_MODEL", "nova-2"), interim_results=True),
        "llm": openai.LLM(model=os.getenv("LLM_MODEL", "gpt-4o-mini")),
        "tts": rime.TTS(
            model=os.getenv("RIME_MODEL", "mistv2"),
            speaker=os.getenv("RIME_SPEAKER", "cove"),
        ),
    }


if __name__ == "__main__":
    from livekit.agents import WorkerOptions, cli

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
