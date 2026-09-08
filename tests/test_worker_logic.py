"""Hermetic tests for the live-audio seam: RimeStreamer facade + InterruptionDetector.

No network, no keys, no LiveKit: the streamer's async seams are stubbed and we assert
the facade contract (speak schedules synthesis, cancel drops chunks + marks mechanism).
Protocol-level behavior (real ws3 clear op, PCM forwarding) is exercised by the
live /ws/call smoke test and evidence/preflight.py.
"""
from __future__ import annotations

import asyncio

import pytest

from agent.stt_pipeline import InterruptionDetector
from agent.tts_rime import RimeStreamer


@pytest.mark.asyncio
async def test_speak_schedules_and_cancel_drops(monkeypatch):
    calls = []

    async def fake_synth(self, token, text, v):
        calls.append(("synth", text, v))

    async def fake_clear(self):
        calls.append("clear")

    monkeypatch.setattr(RimeStreamer, "_synthesize", fake_synth)
    monkeypatch.setattr(RimeStreamer, "_clear", fake_clear)

    tts = RimeStreamer()
    tts.speak("hello", 1)
    await asyncio.sleep(0.01)
    assert ("synth", "hello", 1) in calls
    assert tts.should_forward_chunk() is True   # speaking again re-opens the stream

    tts.cancel()
    await asyncio.sleep(0.01)
    assert "clear" in calls
    assert tts.should_forward_chunk() is False  # chunks dropped after cancel
    assert tts.last_cancel_mechanism == "ws3:clear+drop"


@pytest.mark.asyncio
async def test_cancel_without_connection_is_safe():
    tts = RimeStreamer()  # never connected -> _clear must no-op, not raise
    tts.cancel()
    await asyncio.sleep(0.01)
    assert tts.should_forward_chunk() is False
    assert tts.last_cancel_mechanism == "ws3:clear+drop"


@pytest.mark.asyncio
async def test_done_fires_exactly_once_per_utterance():
    """Natural done + stall watchdog + cancel must not double-fire speech_end."""
    done_count = []

    async def on_done():
        done_count.append(1)

    tts = RimeStreamer(on_done=on_done)
    tts.speak("hello", 1)
    token = tts._current
    # three racing completions for the same utterance
    await tts._fire_done(token)
    await tts._fire_done(token)
    await tts._fire_done(token)
    assert len(done_count) == 1
    assert tts.active is False


@pytest.mark.asyncio
async def test_superseded_utterance_done_does_not_kill_new_one(monkeypatch):
    """A stray done for a CANCELLED utterance must not end the next utterance early."""
    done_count = []

    async def on_done():
        done_count.append(1)

    class _FakeWS:
        closed = False
        async def send_str(self, s): pass

    async def fake_connect(self):
        self._ws = _FakeWS()

    monkeypatch.setattr(RimeStreamer, "_connect", fake_connect)

    tts = RimeStreamer(on_done=on_done)
    tts.speak("first", 1)
    old_token = tts._current
    tts.cancel()
    await asyncio.sleep(0.01)
    assert len(done_count) == 1        # cancel ended utterance 1

    tts.speak("second", 2)             # new utterance, new token (sends on fake ws)
    await asyncio.sleep(0.05)
    await tts._fire_done(old_token)    # stray done from the old one arrives late
    await asyncio.sleep(0.01)
    assert len(done_count) == 1        # must NOT fire for utterance 2
    assert tts.active is True          # utterance 2 still speaking

    tts.cancel()                       # clean up the background watchdog loop
    await asyncio.sleep(0.02)


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
