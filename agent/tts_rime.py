"""Rime TTS wrapper with mid-stream cancellation.

LOAD-BEARING (roadmap Phase 0): the entire claim depends on being able to stop queued
Rime audio when the user interrupts. Two mechanisms, in priority order:

  1. Native cancel — if the LiveKit Rime plugin / synthesized-audio handle exposes a
     stop/interrupt/aclose method, call it. This is the preferred path.
  2. Client-side buffer drop — flip a flag so we stop consuming/forwarding audio chunks
     even if the server-side stream can't be killed. Always available as a fallback.

Whichever path fired is recorded on `.last_cancel_mechanism` and surfaced at /status and
in RIME_EVIDENCE.md — we disclose exactly what was used, never assume native support.
"""
from __future__ import annotations

import os
from typing import Optional


class RimeTTS:
    """Thin cancellable facade over a LiveKit AgentSession / Rime handle.

    `session` is the object that actually plays audio (e.g. livekit.agents AgentSession
    or a Rime synthesize handle). We probe it for a cancel-capable method at runtime so
    this file doesn't hard-depend on a specific plugin API version.
    """

    _NATIVE_METHODS = ("interrupt", "stop", "cancel", "aclose", "clear")

    def __init__(self, session=None, event_logger=None):
        self._session = session
        self._events = event_logger
        self._dropped = False
        self.last_cancel_mechanism: Optional[str] = None
        self.model = os.getenv("RIME_MODEL", "mistv2")
        self.speaker = os.getenv("RIME_SPEAKER", "cove")
        self.lang = os.getenv("RIME_LANG", "eng")

    @property
    def buffer_dropped(self) -> bool:
        return self._dropped

    def speak(self, text: str, turn_version: int) -> None:
        # a fresh utterance re-opens the stream
        self._dropped = False
        if self._session is not None and hasattr(self._session, "say"):
            self._session.say(text)  # livekit AgentSession.say(...)

    def cancel(self) -> None:
        """Stop queued audio as fast as possible. Prefer native, fall back to drop."""
        self._dropped = True  # always stop forwarding chunks immediately
        mechanism = "buffer_drop"
        if self._session is not None:
            for name in self._NATIVE_METHODS:
                fn = getattr(self._session, name, None)
                if callable(fn):
                    try:
                        fn()
                        mechanism = f"native:{name}"
                    except Exception:
                        mechanism = "buffer_drop"
                    break
        self.last_cancel_mechanism = mechanism

    def should_forward_chunk(self) -> bool:
        """Called in the audio-forwarding loop; False after a cancel until next speak()."""
        return not self._dropped
