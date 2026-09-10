"""Capture the exact PCM the browser receives and analyze it for artifacts.

Connects to /ws/call like web/index.html (deflate offered), drives Riya via the
text path, dumps every binary frame to a WAV (plus a frame-boundary index), then
checks the things that sound like "background breaking" during speech:

  * byte-length parity / sample misalignment per frame
  * clicks: |x[n] - x[n-1]| spikes at frame boundaries vs. inside frames
  * clipping and DC offset
  * duplicated content across boundaries (frame sent twice)
  * long zero runs (underrun-shaped silence inside the stream)
"""
import asyncio
import json
import os
import struct
import sys
import time
import wave

import aiohttp

URL = os.getenv("PROBE_URL", "ws://localhost:8000/ws/call")
OUT = os.getenv("PROBE_OUT", "evidence/probe_capture")


async def capture():
    frames = []          # (bytes, arrival)
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL, compress=9, max_msg_size=2**22) as ws:
            state = "greeting"
            while True:
                msg = await ws.receive(timeout=60)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    d = json.loads(msg.data)
                    if d.get("type") == "speech_end":
                        if state == "greeting":
                            state = "reply"
                            await ws.send_str(
                                "Hi Riya, can you tell me everything about booking twelve please?")
                        else:
                            break
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    frames.append((msg.data, time.monotonic()))
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
    return frames


def analyze(frames):
    print(f"frames: {len(frames)}, total bytes: {sum(len(b) for b, _ in frames)}")
    odd = [i for i, (b, _) in enumerate(frames) if len(b) % 2]
    print(f"odd-byte frames (sample misalignment): {len(odd)}"
          + (f" at {odd[:10]}" if odd else ""))

    # concatenated samples + boundary indices
    samples = []
    bounds = []
    for b, _ in frames:
        n = len(b) // 2
        samples.extend(struct.unpack(f"<{n}h", b[:n * 2]))
        bounds.append(len(samples))

    # clicks at boundaries vs inside: distribution of |delta|
    def delta_stats(pairs):
        if not pairs:
            return "n/a"
        d = sorted(abs(a - c) for a, c in pairs)
        return (f"max {d[-1]}, p99 {d[int(len(d)*0.99)]}, "
                f">8000: {sum(x > 8000 for x in d)}, >16000: {sum(x > 16000 for x in d)}")

    at_bounds = [(samples[i - 1], samples[i]) for i in bounds[1:-1] if i > 0]
    inside = [(samples[i - 1], samples[i]) for i in range(1, len(samples))
              if i not in set(bounds)]
    print(f"|delta| AT frame boundaries : {delta_stats(at_bounds)}")
    print(f"|delta| INSIDE frames       : {delta_stats(inside)}")

    # duplicated adjacent frames (same bytes twice -> garbled overlap feel)
    dups = sum(1 for i in range(1, len(frames)) if frames[i][0] == frames[i - 1][0])
    print(f"consecutive identical frames (duplicates): {dups}")

    # clipping / DC
    clip = sum(1 for x in samples if abs(x) >= 32700)
    print(f"clipped samples (|x|>=32700): {clip}  ({100.0*clip/max(1,len(samples)):.4f}%)")
    print(f"DC offset: {sum(samples)/max(1,len(samples)):.1f}")

    # zero runs >= 5ms (silence holes inside the stream that aren't frame gaps)
    zr, run, longest = 0, 0, 0
    for x in samples:
        if x == 0:
            run += 1
            longest = max(longest, run)
        else:
            if run >= 120:
                zr += 1
            run = 0
    if run >= 120:
        zr += 1
    print(f"zero runs >=5ms: {zr}, longest: {longest/24000*1000:.1f}ms")

    # inter-frame arrival holes (context)
    gaps = [round((frames[i + 1][1] - frames[i][1]) * 1000)
            for i in range(len(frames) - 1)]
    big = [g for g in gaps if g > 100]
    print(f"arrival gaps >100ms: {len(big)} {big}")


def write_wav(frames):
    path = OUT + ".wav"
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        for b, _ in frames:
            w.writeframes(b[:len(b) // 2 * 2])
    print(f"wrote {path} ({sum(len(b) for b, _ in frames)//2} samples)")


if __name__ == "__main__":
    fr = asyncio.run(capture())
    write_wav(fr)
    analyze(fr)
