"""Rime streaming TTS client (WebSocket ws3) — no LiveKit, no transport middleman.

This is the audio path the browser hears. Design goals:

  * Lowest latency: `mistv3` (~37 ms TTFB), raw PCM @ 24 kHz, no container/decoder hop.
  * Server-side cancel that is REAL: ws3 supports the documented `clear` operation
    (discards the synthesis buffer) — the mechanism our <300 ms claim measures, plus a
    local chunk-drop so nothing buffered is forwarded.
  * Keeps the exact facade TurnManager expects: `speak(text, turn_version)` and
    `cancel()` are sync entry points that schedule async work on the running loop.

Correctness invariants (each fixes a real failure mode):
  * UTTERANCES ARE SERIALIZED — an asyncio.Lock means one text on the wire at a
    time; a speak() that arrives mid-utterance waits for the old one to end.
  * COMPLETION FIRES EXACTLY ONCE — every exit path (natural done, cancel, stall
    watchdog, connect error, socket death) funnels through _fire_done(token),
    which is once-only and supersede-aware. Without this, a stall + late done
    double-fired speech_end, and connect failures left the caller's busy flag
    stuck True (every later user utterance misread as an interrupt).
  * CANCEL ENDS THE UTTERANCE — cancel() fires done itself: the agent stopped
    talking, so the caller must hear speech_end immediately (not only when Rime
    gets around to it).

Protocol (docs.rime.ai — verified 2026-09-08):
  connect: wss://users-ws.rime.ai/ws3?modelId=..&speaker=..&lang=..&audioFormat=pcm&samplingRate=24000
           auth: `Authorization: Bearer <key>` connection header (server-side only)
  send:    {"text": "..."} | {"operation": "clear"} | {"operation": "eos"}
  recv:    {"type": "chunk", "data": "<base64 pcm>"} ... {"type": "done"}
           (server sends "chunk"; "audio" is also accepted defensively)
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Awaitable, Callable, Optional

_DEFAULT_MODEL = "mistv3"
_DEFAULT_SPEAKER = "cove"
_DEFAULT_LANG = "en"
_SAMPLE_RATE = 24000


class RimeStreamer:
    """One synthesis stream at a time. `on_audio(bytes)` receives raw PCM16 frames."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 speaker: Optional[str] = None, lang: Optional[str] = None,
                 on_audio: Optional[Callable[[bytes], Awaitable[None]]] = None,
                 on_done: Optional[Callable[[], Awaitable[None]]] = None,
                 event_logger=None):
        self._api_key = api_key or os.getenv("RIME_API_KEY", "")
        self.model = model or os.getenv("RIME_MODEL", _DEFAULT_MODEL)
        self.speaker = speaker or os.getenv("RIME_SPEAKER", _DEFAULT_SPEAKER)
        self.lang = lang or os.getenv("RIME_LANG", _DEFAULT_LANG)
        self._on_audio = on_audio
        self._on_done = on_done
        self._events = event_logger
        self._ws: Optional[object] = None
        self._http: Optional[object] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()          # one utterance on the wire at a time
        self._current: object = object()     # token of the utterance being spoken
        self._sent: Optional[object] = None  # token whose text was last sent
        self._fired: Optional[object] = None # token whose done already fired
        self.active = False          # agent is (about to be) speaking
        self._dropped = False        # chunks stop being forwarded after a cancel
        self._last_audio_ts: Optional[float] = None   # updated by the reader
        self._pcm_carry = b""    # 1-byte tail of a sample split across Rime frames
        self.last_cancel_mechanism: Optional[str] = None

    # ---- facade used by TurnManager (sync; schedules on the running loop) ----
    def speak(self, text: str, turn_version: int) -> None:
        token = object()
        self._current = token
        self._dropped = False
        self._last_audio_ts = None
        self.active = True
        asyncio.create_task(self._synthesize(token, text, turn_version))

    def cancel(self) -> None:
        """Stop queued Rime audio: ws3 `clear` (server buffer) + drop local chunks.
        The interrupted utterance is OVER — fire its done immediately so the caller
        clears its busy flag and the browser flushes playback right away."""
        self._dropped = True
        self.active = False
        asyncio.create_task(self._clear())
        asyncio.create_task(self._fire_done(self._current))
        self.last_cancel_mechanism = "ws3:clear+drop"

    def should_forward_chunk(self) -> bool:
        return not self._dropped

    # ---- completion (once-only, supersede-aware) ------------------------------
    async def _fire_done(self, token: object) -> None:
        if token is not self._current or token is self._fired:
            return  # superseded by a newer speak(), or already fired
        self._fired = token
        self.active = False
        if self._on_done:
            try:
                res = self._on_done()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    # ---- async internals -----------------------------------------------------
    async def warm_up(self) -> None:
        """Open the TTS socket ahead of the first speak(). The ws3 handshake costs
        ~1.3s (measured); paying it at session start instead of inside the first
        _synthesize() shaves it off the greeting's time-to-first-audio. Safe to
        race with speak(): both serialize on the same lock."""
        if self._ws is not None and not self._ws.closed:
            return
        async with self._lock:
            if self._ws is None or self._ws.closed:
                try:
                    await self._connect()
                except Exception as e:
                    if self._events:
                        self._events.log("tts_warmup_error", error=str(e)[:200])

    async def _connect(self) -> None:
        import aiohttp

        url = ("wss://users-ws.rime.ai/ws3"
               f"?modelId={self.model}&speaker={self.speaker}&lang={self.lang}"
               f"&audioFormat=pcm&samplingRate={_SAMPLE_RATE}")
        http = aiohttp.ClientSession()
        try:
            ws = await http.ws_connect(
                url, headers={"Authorization": f"Bearer {self._api_key}"}, heartbeat=30)
        except Exception:
            await http.close()   # a failed handshake must not leak the session
            raise
        self._http = http
        self._ws = ws
        self._pcm_carry = b""   # byte stream restarts with each socket
        self._reader_task = asyncio.create_task(self._read_loop())

    async def _synthesize(self, token: object, text: str, turn_version: int) -> None:
        async with self._lock:  # serialize: previous utterance fully ends first
            if self._events:
                self._events.log("agent_speech_start", turn_version=turn_version)
            if self._ws is None:
                try:
                    await self._connect()
                except Exception as e:
                    if self._events:
                        self._events.log("tts_error", error=str(e)[:200])
                    await self._fire_done(token)
                    return
            if self._ws is None or self._ws.closed or not self.active:
                # socket dead, or cancelled while we were connecting/sending
                await self._fire_done(token)
                return
            self._dropped = False       # reset only now: any pre-lock chunks were
            self._last_audio_ts = None  # from the old (already ended) utterance
            try:
                await self._ws.send_str(json.dumps({"text": text}))
            except Exception as e:
                if self._events:
                    self._events.log("tts_error", error=str(e)[:200])
                await self._fire_done(token)
                return
            self._sent = token
            # watchdog: Rime can silently stall (free-tier throttling, transient
            # outages). If NO audio arrives within 8s, audio stops mid-utterance for
            # 15s, or the utterance exceeds 60s — end it so the call recovers.
            sent_at = time.monotonic()
            while self.active and token is self._current:
                now = time.monotonic()
                if self._last_audio_ts is None and now - sent_at > 8.0:
                    if self._events:
                        self._events.log("tts_stall", detail="no audio within 8s",
                                          turn_version=turn_version)
                    break
                if self._last_audio_ts is not None and now - self._last_audio_ts > 15.0:
                    if self._events:
                        self._events.log("tts_stall", detail="audio stalled 15s",
                                          turn_version=turn_version)
                    break
                if now - sent_at > 60.0:
                    if self._events:
                        self._events.log("tts_stall", detail="utterance > 60s",
                                          turn_version=turn_version)
                    break
                await asyncio.sleep(0.05)
            await self._fire_done(token)  # no-op if reader/cancel already fired

    async def _read_loop(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type.name != "TEXT":  # aiohttp enum, not a plain string
                    continue
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                if data.get("type") in ("chunk", "audio") and data.get("data"):
                    if self._dropped:
                        self._pcm_carry = b""  # cancelled stream: a pending half-
                        continue                # sample never arrives; don't leak it
                    self._last_audio_ts = time.monotonic()
                    raw = base64.b64decode(data["data"])
                    # Rime splits PCM16 SAMPLES across frame boundaries — odd-byte
                    # frames are routine (measured: ~34 per 12s; a straddle leaves
                    # every following frame byte-shifted -> static bursts at the
                    # listener). Re-chunk with a 1-byte carry so every forwarded
                    # frame is sample-aligned; the byte stream is unchanged.
                    # (evidence/probe_straddle.py: seam deltas 34509 -> 4972)
                    frame = self._pcm_carry + raw
                    if len(frame) % 2:
                        self._pcm_carry = frame[-1:]
                        frame = frame[:-1]
                    else:
                        self._pcm_carry = b""
                    if self._on_audio:
                        try:
                            res = self._on_audio(frame)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:
                            pass  # a bad callback must not kill the reader loop
                elif data.get("type") == "done":
                    if self._events:
                        self._events.log("agent_speech_end")
                    # fire for the utterance whose text we actually sent — a stray
                    # done from a superseded/cancelled utterance must not end the
                    # next one early
                    if self._sent is not None:
                        await self._fire_done(self._sent)
        except Exception:
            pass
        finally:
            self.active = False
            if self._sent is not None:
                await self._fire_done(self._sent)  # socket died mid-utterance: end it

    async def _clear(self) -> None:
        self._pcm_carry = b""    # server discards its buffer: pending half-sample is gone
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_str(json.dumps({"operation": "clear"}))
        except Exception:
            pass

    async def close(self) -> None:
        try:
            await self._clear()
            if self._ws is not None:
                try:
                    await self._ws.send_str(json.dumps({"operation": "eos"}))
                except Exception:
                    pass
                await self._ws.close()
        except Exception:
            pass
        self._ws = None
        self.active = False
        if self._http is not None:
            try:
                await self._http.close()
            except Exception:
                pass
            self._http = None
