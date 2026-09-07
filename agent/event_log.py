"""Structured JSON event logger matching roadmap §5.4.

Times are milliseconds relative to session start, from a monotonic clock. The dict
returned by `log()` is the SAME object stored in `events`, so callers may enrich it
in place (e.g. attaching a computed `latency_ms` to a `tts_cancel` event).
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional


class EventLogger:
    def __init__(self, session_id: str, clock: Optional[Callable[[], float]] = None):
        self.session_id = session_id
        self._clock = clock or time.monotonic
        self._t0 = self._clock()
        self.events: list[dict] = []

    def now_ms(self) -> float:
        # fractional ms: the orchestration cancel path is sub-millisecond, and rounding
        # it to an int would misleadingly print "0" — keep real precision.
        return round((self._clock() - self._t0) * 1000, 4)

    def log(self, type: str, **fields) -> dict:
        ev = {"t_ms": self.now_ms(), "type": type}
        ev.update(fields)
        self.events.append(ev)
        return ev

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "events": self.events}

    def write(self, path: str) -> str:
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path
