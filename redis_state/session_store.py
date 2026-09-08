"""Per-session state in Redis — the differentiator's backing store.

Keys (per roadmap §2):
  session:{id}:turn_version   int, incremented on every detected interruption
  session:{id}:pending_tools  hash of tool_call_id -> version tagged at dispatch
  session:{id}:state          last confirmed booking state (JSON), only non-stale writes

Works against real redis (REDIS_URL) or in-process fakeredis (hermetic tests). The
turn-versioning logic is identical for both.
"""
from __future__ import annotations

import json
import os
from typing import Optional


def make_redis_client(url: Optional[str] = None, use_fake: Optional[bool] = None):
    url = url or os.getenv("REDIS_URL")
    if use_fake is None:
        env_fake = os.getenv("USE_FAKE_REDIS", "").lower() in ("1", "true", "yes")
        use_fake = env_fake or not url
    if use_fake:
        import fakeredis

        return fakeredis.FakeStrictRedis(decode_responses=True)
    import redis

    return redis.Redis.from_url(url, decode_responses=True)


class SessionStore:
    def __init__(self, client, session_id: str):
        self.r = client
        self.sid = session_id

    def _k(self, suffix: str) -> str:
        return f"session:{self.sid}:{suffix}"

    def create_session(self) -> None:
        # turn_version starts at 1 for the first turn (matches §5.4 event schema)
        self.r.setnx(self._k("turn_version"), 1)

    def get_turn_version(self) -> int:
        v = self.r.get(self._k("turn_version"))
        return int(v) if v is not None else 0

    def increment_turn_version(self) -> int:
        return int(self.r.incr(self._k("turn_version")))

    def register_pending_tool(self, tool_call_id: str, version: int) -> None:
        self.r.hset(self._k("pending_tools"), tool_call_id, int(version))

    def pop_pending_tool(self, tool_call_id: str) -> Optional[int]:
        key = self._k("pending_tools")
        v = self.r.hget(key, tool_call_id)
        if v is None:
            return None
        self.r.hdel(key, tool_call_id)
        return int(v)

    def get_pending_tools(self) -> dict:
        return {k: int(v) for k, v in (self.r.hgetall(self._k("pending_tools")) or {}).items()}

    def set_confirmed_state(self, state: dict) -> None:
        self.r.set(self._k("state"), json.dumps(state, default=str))

    def get_confirmed_state(self) -> Optional[dict]:
        v = self.r.get(self._k("state"))
        return json.loads(v) if v else None

    def reset(self) -> None:
        self.r.delete(self._k("turn_version"), self._k("pending_tools"), self._k("state"))
