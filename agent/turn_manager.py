"""Turn-versioning orchestration — the core differentiator (roadmap Phase 3).

Rules:
  * Every tool dispatch is tagged with the turn_version active at dispatch time.
  * A detected interruption increments turn_version and cancels queued TTS immediately.
  * On tool-result arrival, the tagged version is compared to the current version:
        stale  (tagged != current) -> logged `discarded_stale`, never spoken, never committed.
        fresh  (tagged == current) -> applied: mutations committed to Postgres, then spoken.

The manager is transport-agnostic: `tts`, `store`, `events`, and `tools` are injected, so
the same logic runs under the hermetic test and under the live LiveKit worker.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Callable, Optional

MUTATING_TOOLS = {"update_booking", "cancel_booking"}


class TurnManager:
    def __init__(self, session_id, store, events, tts, tools,
                 render: Optional[Callable[[str, dict], str]] = None):
        self.session_id = session_id
        self.store = store
        self.events = events
        self.tts = tts
        self.tools = tools
        self._render = render or self._default_render
        self._tasks: list[asyncio.Task] = []
        self.store.create_session()

    # -- turn boundaries ----------------------------------------------------
    def on_user_speech_start(self, transcript: str = "") -> int:
        v = self.store.get_turn_version()
        self.events.log("user_speech_start", turn_version=v, transcript=transcript)
        return v

    def on_interrupt(self, reason: str = "interrupt") -> int:
        new_version = self.store.increment_turn_version()
        interrupt_ev = self.events.log("user_speech_start_interrupt", turn_version=new_version)
        # stop queued Rime audio as fast as possible
        self.tts.cancel()
        cancel_ev = self.events.log("tts_cancel", reason=reason)
        cancel_ev["latency_ms"] = round(cancel_ev["t_ms"] - interrupt_ev["t_ms"], 4)
        return new_version

    # -- tool dispatch / result --------------------------------------------
    async def dispatch_tool(self, tool_name: str, args: dict) -> str:
        version = self.store.get_turn_version()
        tool_call_id = "tc_" + uuid.uuid4().hex[:8]
        self.store.register_pending_tool(tool_call_id, version)
        self.events.log("tool_call_dispatch", tool=tool_name,
                        turn_version=version, tool_call_id=tool_call_id)
        task = asyncio.create_task(self._execute(tool_name, args, version, tool_call_id))
        self._tasks.append(task)
        return tool_call_id

    async def _execute(self, tool_name, args, version, tool_call_id):
        result = await self.tools.run(tool_name, args, version)
        await self._on_tool_result(tool_name, tool_call_id, result)

    async def _on_tool_result(self, tool_name, tool_call_id, result):
        tagged = self.store.pop_pending_tool(tool_call_id)
        version_at_result = result.get("turn_version", tagged)
        current = self.store.get_turn_version()

        if version_at_result != current:
            self.events.log("tool_call_result", tool=tool_name, tool_call_id=tool_call_id,
                            turn_version_at_result=version_at_result,
                            current_turn_version=current, action="discarded_stale")
            return

        # fresh -> apply. Mutations are committed ONLY here, so stale writes never persist.
        if tool_name in MUTATING_TOOLS:
            payload = await self.tools.commit(result["proposal"])
        else:
            payload = result.get("booking", result.get("proposal"))
        self.store.set_confirmed_state(payload)
        self.events.log("tool_call_result", tool=tool_name, tool_call_id=tool_call_id,
                        turn_version_at_result=version_at_result,
                        current_turn_version=current, action="applied")
        self.tts.speak(self._render(tool_name, payload), current)

    async def join(self):
        if self._tasks:
            await asyncio.gather(*self._tasks)
            self._tasks = []

    @staticmethod
    def _default_render(tool_name: str, payload: dict) -> str:
        return f"{tool_name} -> {payload}"
