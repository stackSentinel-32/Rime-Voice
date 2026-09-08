#!/usr/bin/env python3
"""Reproduce the FULL browser mic path with REAL speech, server-side.

Faithful to production: exactly ONE Rime connection per call (the session's own
streamer) — the user's "speech" is synthesized on the SAME ws, captured, then fed
through Deepgram via feed_audio, exactly like the browser sends mic PCM.

Run inside the backend container:
    docker cp evidence/check_stt.py rime-voice-backend-1:/tmp/check_stt.py
    docker compose exec -T backend python /tmp/check_stt.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, "/app")  # backend container WORKDIR
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import Base, make_engine, make_session_factory  # noqa: E402
from backend.seed import seed  # noqa: E402
from backend.voice import VoiceCallSession  # noqa: E402


def decimate_24k_to_16k(pcm24: bytes) -> bytes:
    n = len(pcm24) // 2
    out = bytearray(int(n * 2 / 3) * 2)
    for i in range(len(out) // 2):
        pos = i * 1.5
        i0 = min(int(pos), n - 1)
        i1 = min(i0 + 1, n - 1)
        frac = pos - i0
        s0 = int.from_bytes(pcm24[i0 * 2:i0 * 2 + 2], "little", signed=True)
        s1 = int.from_bytes(pcm24[i1 * 2:i1 * 2 + 2], "little", signed=True)
        v = int(s0 * (1 - frac) + s1 * frac)
        out[i * 2:i * 2 + 2] = v.to_bytes(2, "little", signed=True)
    return bytes(out)


async def main() -> int:
    engine = make_engine()
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    seed(sf, n=40, reset=False)

    msgs = []
    reply_bytes = bytearray()

    async def send(m):
        msgs.append(m)
        print(f"  [browser<-json] {m.get('type')}")

    async def send_audio(pcm):
        reply_bytes.extend(pcm)

    print("=== 1. create session (greeting streams on the ONE Rime ws) ===")
    sess = VoiceCallSession(send, send_audio, sf, session_id="check-stt")
    await sess.start()
    t0 = time.time()
    while sess.tts.active and time.time() - t0 < 15:
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.3)
    print(f"   greeting audio bytes: {len(reply_bytes)}")

    print("=== 2. synthesize user speech on the SAME ws, capture it ===")
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Check my booking twelve please"
    user_speech = bytearray()
    orig_on_audio = sess.tts._on_audio
    async def collect(b):
        user_speech.extend(b)
    sess.tts._on_audio = collect
    sess.tts.speak(prompt, sess.store.get_turn_version())
    t0 = time.time()
    while sess.tts.active and time.time() - t0 < 15:
        await asyncio.sleep(0.1)
    sess.tts._on_audio = orig_on_audio
    pcm24 = bytes(user_speech)
    print(f"   user speech bytes: {len(pcm24)}")
    if not pcm24:
        print("FAIL: could not synthesize user speech"); return 1
    pcm16 = decimate_24k_to_16k(pcm24)

    print("=== 3. feed through Deepgram in 20ms chunks (browser-equivalent) ===")
    chunk = 320  # 20ms @ 16k
    for i in range(0, len(pcm16), chunk):
        await sess.feed_audio(pcm16[i:i + chunk])
        await asyncio.sleep(0.02)
    for _ in range(25):  # tail silence -> endpointing (400ms) finalizes
        await sess.feed_audio(b"\x00\x00" * 320)
        await asyncio.sleep(0.02)

    print("=== 4. waiting for reply (up to 30s) ===")
    before = len(reply_bytes)
    t0 = time.time()
    while len(reply_bytes) - before < 2000 and time.time() - t0 < 30:
        await asyncio.sleep(0.2)
    new_audio = len(reply_bytes) - before
    print(f"reply audio bytes: {new_audio}")
    print("RESULT:", "PASS" if new_audio > 2000 else "FAIL")
    await sess.close()
    return 0 if new_audio > 2000 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
