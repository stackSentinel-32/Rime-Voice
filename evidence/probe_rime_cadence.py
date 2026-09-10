"""Measure Rime ws3 streaming cadence: is audio delivered faster than realtime?

Emits per-chunk timing for a short and a long utterance (same triple as the app).
Run inside the backend container:  docker exec rime-voice-backend-1 python evidence/probe_rime_cadence.py
"""
import asyncio
import base64
import json
import os
import time

import aiohttp

URL = ("wss://users-ws.rime.ai/ws3"
       f"?modelId={os.getenv('RIME_MODEL', 'mistv3')}"
       f"&speaker={os.getenv('RIME_SPEAKER', 'cove')}"
       f"&lang={os.getenv('RIME_LANG', 'en')}"
       "&audioFormat=pcm&samplingRate=24000")


async def probe(session, label, text):
    t0 = time.monotonic()
    chunks = []          # (arrival_s, audio_ms)
    ws = await session.ws_connect(
        URL, headers={"Authorization": f"Bearer {os.environ['RIME_API_KEY']}"}, heartbeat=30)
    ttfb_conn = time.monotonic() - t0
    await ws.send_str(json.dumps({"text": text}))
    t_send = time.monotonic()
    done_at = None
    async for msg in ws:
        if msg.type.name != "TEXT":
            continue
        data = json.loads(msg.data)
        if data.get("type") in ("chunk", "audio") and data.get("data"):
            raw = base64.b64decode(data["data"])
            chunks.append((time.monotonic() - t_send, len(raw) / 2 / 24000 * 1000))
        elif data.get("type") == "done":
            done_at = time.monotonic() - t_send
            break
    await ws.close()

    total_audio_ms = sum(a for _, a in chunks)
    ttfb = chunks[0][0] if chunks else float("nan")
    span = done_at if done_at else (chunks[-1][0] if chunks else 0)
    # rate >1.0 means audio arrives faster than it plays back (healthy for streaming)
    rate = total_audio_ms / (span - ttfb) if len(chunks) > 1 and span > ttfb else float("nan")
    gaps = [chunks[i + 1][0] - chunks[i][0] for i in range(len(chunks) - 1)]
    big = [g for g in gaps if g > 0.1]

    print(f"\n=== {label}: {len(chunks)} chunks ===")
    print(f"  connect: {ttfb_conn*1000:.0f} ms   TTFB: {ttfb*1000:.0f} ms")
    print(f"  audio: {total_audio_ms:.0f} ms over {span*1000:.0f} ms wall  "
          f"-> delivery rate {rate:.2f}x realtime")
    print(f"  inter-chunk gaps: median {sorted(gaps)[len(gaps)//2]*1000:.1f} ms, "
          f"max {max(gaps)*1000:.0f} ms, >100ms count: {len(big)}")
    if big:
        print(f"  gaps>100ms (ms): {[round(g*1000) for g in big]}")
    # cumulative arrival vs playback clock at 10 points: negative = ahead of realtime
    marks = []
    for frac in (0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
        idx = min(int(len(chunks) * frac), len(chunks) - 1)
        at, _ = chunks[idx]
        audio_by = sum(a for _, a in chunks[: idx + 1])
        marks.append(f"{int(frac*100)}%: {audio_by - at*1000:+.0f}ms")
    print(f"  buffer lead at utterance fraction (audio_arrived - wall_elapsed): {', '.join(marks)}")


async def main():
    async with aiohttp.ClientSession() as session:
        await probe(session, "SHORT (greeting-like)",
                    "Hi, I'm Riya, your booking assistant. How can I help you today?")
        await asyncio.sleep(1)
        await probe(session, "LONG (confirmation-like)",
                    "I found your booking. It's a haircut appointment on Thursday "
                    "September seventeenth at two thirty in the afternoon, currently "
                    "confirmed. Would you like me to change the date, the time, or "
                    "perhaps cancel it entirely? Just let me know what works best.")


asyncio.run(main())
