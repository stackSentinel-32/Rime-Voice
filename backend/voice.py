"""One live voice call — the composition root, no LiveKit.

Browser PCM → DeepgramStreamer (partials) → InterruptionDetector → TurnManager
→ tools (backend) → RimeStreamer (ws3, PCM 24k) → WebSocket back to browser.

Every final transcript runs an LLM conversation turn (persona "Riya"):
  * tool call  -> dispatched through TurnManager (versioned, stale-discarded);
                  the fresh result is confirmed in natural language via reply_hook.
  * text reply -> spoken directly, but ONLY if the turn version is still current
                  (an interruption while the LLM is thinking cancels the reply).

PERFORMANCE INVARIANT: browser sends go through an outbound QUEUE drained by a
writer task — the Rime/Deepgram reader loops NEVER await the browser socket. A slow
tab must never backpressure the reader and stall replies.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from agent.event_log import EventLogger
from agent.llm_tools import confirmation_turn, conversation_turn, make_llm
from agent.stt_pipeline import DeepgramStreamer, InterruptionDetector
from agent.tool_client import LocalToolClient
from agent.tts_rime import RimeStreamer
from agent.turn_manager import TurnManager
from redis_state.session_store import SessionStore, make_redis_client


class VoiceCallSession:
    def __init__(self, send, send_audio, session_factory, session_id: Optional[str] = None):
        """`send(dict)` pushes a JSON control message to the browser;
        `send_audio(bytes)` pushes raw PCM16 frames (24 kHz) to the browser."""
        self._send = send
        self._send_audio = send_audio
        self.sid = session_id or "call-" + uuid.uuid4().hex[:8]
        self.events = EventLogger(self.sid)
        self.store = SessionStore(make_redis_client(), self.sid)
        self.store.reset()
        self._out: asyncio.Queue = asyncio.Queue()
        self._writer_task = asyncio.create_task(self._writer())
        self.history: list[str] = []
        self.llm = make_llm()
        self.tts = RimeStreamer(
            on_audio=self.send_audio,
            on_done=self._speech_done,
            event_logger=self.events,
        )
        self.tm = TurnManager(
            self.sid, self.store, self.events, self.tts,
            LocalToolClient(session_factory),
            render=lambda n, p: f"Booking {p.get('id')} is now on {p.get('date')} ({p.get('status')}).",
            reply_hook=self._confirm_tool,
        )
        self.detector = InterruptionDetector(self.tm)
        self.stt = DeepgramStreamer(
            on_transcript=self._on_stt, event_logger=self.events)

    # ---- outbound (never awaited by the audio readers) ----------------------
    # Audio is DROPS-not-BLOCKS: voice must never backpressure the reader. If the
    # browser can't keep up we shed queued frames rather than stall synthesis,
    # then resume once the writer drains back below the cap.
    # ~2s of 24kHz/16bit audio in flight: absorbs main-thread hiccups without ever
    # dropping speech (only sheds past a 2s backlog)
    _MAX_QUEUED_AUDIO = 96

    async def _writer(self) -> None:
        while True:
            kind, payload = await self._out.get()
            try:
                if kind == "audio":
                    await self._send_audio(payload)
                else:
                    await self._send(payload)
            except Exception:
                pass  # browser gone: keep draining so the reader never stalls

    async def send_audio(self, pcm: bytes) -> None:
        # never await the socket in the reader: enqueue iff there's room, else drop
        if self._out.qsize() < self._MAX_QUEUED_AUDIO:
            self._out.put_nowait(("audio", pcm))

    async def send_json_msg(self, msg: dict) -> None:
        self._out.put_nowait(("json", msg))

    # ---- browser-facing helpers --------------------------------------------
    async def start(self) -> None:
        self.detector.set_agent_busy(True)
        try:
            await self.stt.open()  # STT WebSocket must be live before audio flows
        except Exception as e:
            self.events.log("stt_open_error", error=str(e)[:200])
        await self.send_json_msg({"type": "ready"})
        self.tts.speak(
            "Hi, I'm Riya, your booking assistant. Try: check my booking twelve — and interrupt me any time.",
            self.store.get_turn_version(),
        )

    async def feed_audio(self, pcm: bytes) -> None:
        await self.stt.send_audio(pcm)

    async def feed_text(self, text: str) -> None:
        """Text-in path (type-to-speak / smoke test): treated as a final transcript."""
        await self._on_stt(text, True)

    async def close(self) -> None:
        await self.stt.close()
        await self.tts.close()
        self._writer_task.cancel()

    # ---- pipeline ------------------------------------------------------------
    async def _on_stt(self, text: str, is_final: bool) -> None:
        # echo partials to the browser so the user can see their speech is being heard
        if text.strip():
            await self.send_json_msg({"type": "transcript", "text": text, "final": is_final})
        print(f"[voice] stt {'final' if is_final else 'part'}: {text!r}")
        fired = self.detector.on_user_speech(text, is_final)
        if is_final:
            # ALWAYS run the turn on a final transcript — even when it just fired an
            # interrupt. A short answer ("10am") can land on its final while the agent
            # is still speaking; dropping it meant the answer that interrupted Riya
            # was never processed and the pending create got stale-discarded with no
            # confirmation. Barge-in semantics: stop AND take the new request.
            asyncio.create_task(self._run_turn(text))

    async def _run_turn(self, transcript: str) -> None:
        if self.llm is None:
            self.events.log("llm_unavailable", hint="GROQ_API_KEY not set")
            return
        version = self.store.get_turn_version()   # gate: reply must match this version
        try:
            decision = await asyncio.to_thread(
                conversation_turn, self.llm, transcript, self.history)
        except Exception as e:
            self.events.log("llm_error", error=str(e)[:200])
            print(f"[voice] llm error: {e}")
            return
        print(f"[voice] decision: {decision}")
        if "tool" in decision:
            self.detector.set_agent_busy(True)
            self.history.append(transcript)
            await self.tm.dispatch_tool(decision["tool"], decision["args"])
            # the fresh tool result speaks via reply_hook (_confirm_tool); stale is silent
        else:
            self.history.append(transcript)
            self.history.append(decision["text"])
            if self.store.get_turn_version() == version:  # not interrupted while thinking
                self.detector.set_agent_busy(True)        # barge-in works during this reply
                print(f"[voice] speaking: {decision['text']!r}")
                self.tts.speak(decision["text"], version)
            else:
                print(f"[voice] DROPPED reply (version {version} -> {self.store.get_turn_version()})")

    async def _speech_done(self) -> None:
        """Rime stream finished -> agent no longer talking; clear the busy flag so
        the NEXT user utterance starts a fresh turn instead of an interrupt."""
        self.detector.set_agent_busy(False)
        await self.send_json_msg({"type": "speech_end"})

    async def _confirm_tool(self, tool_name: str, payload) -> str:
        """reply_hook: natural-language confirmation for a fresh (non-stale) tool result."""
        if self.llm is None:
            return f"Done. {tool_name} result: {payload}"
        try:
            text = await asyncio.to_thread(
                confirmation_turn, self.llm, tool_name, payload, self.history)
        except Exception as e:
            self.events.log("llm_error", error=str(e)[:200])
            return f"Done. {tool_name} result: {payload}"
        self.history.append(f"tool {tool_name} -> {payload}")
        return text
