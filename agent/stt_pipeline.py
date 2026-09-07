"""Streaming STT config + the interruption-detection hook.

Partial transcripts (not just finals) are required: interruption detection must be fast,
and waiting for a 'final' transcript is too slow (roadmap §2). The active provider is
env-selected (STT_PROVIDER) and surfaced at /status so a judge can verify it.

`InterruptionDetector` is the seam between VAD/STT and the TurnManager: when the user
starts speaking *while the agent is playing audio or a tool call is pending*, it fires
`turn_manager.on_interrupt()`. Silence / sub-threshold noise must NOT fire it.
"""
from __future__ import annotations

import os


def stt_config() -> dict:
    provider = os.getenv("STT_PROVIDER", "deepgram")
    if provider == "whisper":
        return {"provider": "whisper", "model": os.getenv("WHISPER_MODEL", "base"),
                "interim_results": True}
    return {"provider": "deepgram", "model": os.getenv("DEEPGRAM_MODEL", "nova-2"),
            "interim_results": True, "endpointing": 25}


class InterruptionDetector:
    """Decides whether user speech during agent activity counts as an interruption."""

    def __init__(self, turn_manager, min_chars: int = 1):
        self.tm = turn_manager
        self.min_chars = min_chars
        self._agent_busy = False

    def set_agent_busy(self, busy: bool) -> None:
        """True while Rime is playing or a tool call is pending."""
        self._agent_busy = busy

    def on_user_speech(self, transcript: str, is_final: bool) -> bool:
        """Called on every partial/final. Returns True if an interruption was fired."""
        text = (transcript or "").strip()
        if len(text) < self.min_chars:
            return False  # silence / noise -> never bump the version
        if self._agent_busy:
            self.tm.on_interrupt(reason="barge_in")
            self._agent_busy = False
            return True
        self.tm.on_user_speech_start(transcript=text)
        return False
