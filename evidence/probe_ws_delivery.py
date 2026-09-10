"""Host-side delivery probe: measure WS audio frame arrival gaps, browser-style.

Connects to the /ws/call endpoint exactly like web/index.html does, optionally
negotiating permessage-deflate (browsers do this by default; the aiohttp client
does not). Sends no mic audio; drives Riya via the text path. Reports the
inter-frame gap profile of the greeting and one tool-driven reply, including
WHERE in the utterance the big holes land (early trickle vs mid-speech) and the
cumulative buffer-lead profile (audio arrived minus wall clock).

Usage:
  python probe_ws_delivery.py 0   # plain WS (no compression)
  python probe_ws_delivery.py 1   # negotiate permessage-deflate (browser-like)
  PROBE_URL=ws://host:port/ws/call python probe_ws_delivery.py 0
"""
import asyncio
import json
import os
import sys
import time

import aiohttp

URL = os.getenv("PROBE_URL", "ws://localhost:8000/ws/call")


def lead_profile(frames):
    t0 = frames[0][0]
    marks = []
    for frac in (0.1, 0.25, 0.5, 0.75, 0.9):
        idx = min(int(len(frames) * frac), len(frames) - 1)
        audio_by = sum(s for _, s in frames[: idx + 1]) / 48000.0
        marks.append(f"{int(frac*100)}%:{audio_by - (frames[idx][0] - t0):+.2f}s")
    return " ".join(marks)


def gapstat(frames):
    gaps = [(frames[i + 1][0] - frames[i][0], i) for i in range(len(frames) - 1)]
    if not gaps:
        return "no frames"
    gs = sorted(g for g, _ in gaps)
    audio_s = sum(s for _, s in frames) / 48000.0
    big = [(round(g * 1000), round(100 * i / len(frames)))
           for g, i in gaps if g > 0.1]
    return (f"{len(frames)} frames, {audio_s:.2f}s audio | gaps ms: "
            f"med {gs[len(gs)//2]*1000:.1f}  p95 {gs[int(len(gs)*0.95)]*1000:.0f}  "
            f"max {max(gs)*1000:.0f} | >100ms: {len(big)} at {big}\n"
            f"    lead(audio-arrived - wall): {lead_profile(frames)}")


async def run(compress: int):
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL, compress=(9 if compress else 0), max_msg_size=2**22) as ws:
            print(f"negotiated compression: {getattr(ws, 'compression', 'n/a')!r}")
            greet, reply = [], []
            state = "greeting"
            t_ready = t_first = None
            while True:
                msg = await ws.receive(timeout=60)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    d = json.loads(msg.data)
                    if d.get("type") == "ready":
                        t_ready = time.monotonic()
                    elif d.get("type") == "speech_end":
                        if state == "greeting":
                            ttfb = (t_first - t_ready) * 1000 if t_first and t_ready else -1
                            print(f"GREETING ({'deflate' if compress else 'plain'}): "
                                  f"ttfb {ttfb:.0f}ms | {gapstat(greet)}")
                            state = "reply"
                            await ws.send_str(
                                "Hi Riya, can you tell me everything about booking twelve please?")
                        else:
                            print(f"REPLY   ({'deflate' if compress else 'plain'}): {gapstat(reply)}")
                            break
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    now = time.monotonic()
                    if state == "greeting":
                        if t_first is None:
                            t_first = now
                        greet.append((now, len(msg.data)))
                    else:
                        reply.append((now, len(msg.data)))
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    print("socket closed early")
                    break


if __name__ == "__main__":
    asyncio.run(run(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
