"""Hermetic tests for the live-audio seam: RimeTTS cancel wrapper + InterruptionDetector.

Neither module imports livekit at import time, so we can test the cancellation
fallback logic and the noise-gate without any heavy deps or API keys.
"""
from __future__ import annotations

import asyncio

from agent.stt_pipeline import InterruptionDetector
from agent.tts_rime import RimeTTS


class _FakeLiveKitSession:
    """Emulates the subset of the livekit session API RimeTTS probes for."""

    def __init__(self, fail_native: bool = False):
        self.interrupted = False
        self.said = []
        self.fail_native = fail_native

    def say(self, text):
        self.said.append(text)

    def interrupt(self):
        if self.fail_native:
            raise RuntimeError("native cancel unsupported")
        self.interrupted = True


def test_native_cancel_preferred():
    lk = _FakeLiveKitSession()
    tts = RimeTTS(session=lk)
    tts.speak("hello", 1)
    tts.cancel()
    assert lk.interrupted
    assert tts.last_cancel_mechanism == "native:interrupt"
    assert tts.should_forward_chunk() is False


def test_buffer_drop_fallback_when_native_fails():
    lk = _FakeLiveKitSession(fail_native=True)
    tts = RimeTTS(session=lk)
    tts.speak("hello", 1)
    tts.cancel()
    assert tts.last_cancel_mechanism == "buffer_drop"
    assert tts.should_forward_chunk() is False
    # a fresh speak() re-opens the stream
    tts.speak("after", 2)
    assert tts.should_forward_chunk() is True


def test_buffer_drop_without_livekit_session():
    tts = RimeTTS(session=None)
    tts.speak("hello", 1)
    tts.cancel()
    assert tts.last_cancel_mechanism == "buffer_drop"


def test_detector_noise_does_not_interrupt(make_tm):
    tm, store, events, tts = make_tm("detect-noise")
    det = InterruptionDetector(tm)
    det.set_agent_busy(True)
    v = store.get_turn_version()
    # silence / sub-threshold partials -> no version bump, no cancel
    assert det.on_user_speech("", is_final=False) is False
    assert det.on_user_speech(" ", is_final=False) is False
    assert store.get_turn_version() == v
    assert tts.cancel_count == 0


def test_detector_barge_in_fires_interrupt(make_tm):
    tm, store, events, tts = make_tm("detect-barge")
    det = InterruptionDetector(tm)
    det.set_agent_busy(True)
    fired = det.on_user_speech("actually Friday", is_final=False)
    assert fired is True
    assert store.get_turn_version() == 2
    assert tts.cancel_count == 1


def test_detector_idle_speech_is_new_turn(make_tm):
    tm, store, events, tts = make_tm("detect-idle")
    det = InterruptionDetector(tm)
    det.set_agent_busy(False)
    fired = det.on_user_speech("check my booking", is_final=True)
    assert fired is False
    assert store.get_turn_version() == 1  # logged as speech start, not interrupt
    evs = events.to_dict()["events"]
    assert evs[-1]["type"] == "user_speech_start"


def test_full_worker_path_interrupt_mid_tool(make_tm, db_sf):
    """Detector + TurnManager together: barge-in during a pending tool call."""
    tm, store, events, tts = make_tm("worker-path")
    det = InterruptionDetector(tm)

    async def scenario():
        det.set_agent_busy(True)
        await tm.dispatch_tool("lookup_booking", {"booking_id": 12, "delay_ms": 200})
        await asyncio.sleep(0.03)
        det.on_user_speech("actually Friday", is_final=False)   # barge-in -> v2
        await tm.dispatch_tool("update_booking", {"booking_id": 12, "changes": {"date": "2026-09-18"}})
        await tm.join()

    asyncio.run(scenario())
    assert store.get_turn_version() == 2
    results = [e for e in events.to_dict()["events"] if e["type"] == "tool_call_result"]
    assert sorted(e["action"] for e in results) == ["applied", "discarded_stale"]
    assert tts.spoken[-1][1] == 2
