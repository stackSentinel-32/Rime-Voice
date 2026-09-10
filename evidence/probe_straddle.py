"""Verify the sample-straddle hypothesis: Rime ws3 splits PCM16 samples across
frame boundaries (odd-byte frames). Decoding frames independently byte-shifts
every frame after a straddle -> ~28ms static bursts ("background breaking").

Test: decode the same captured bytes two ways and compare seam continuity.
  A) per-frame decode (what the browser worklet does today)
  B) continuous decode (concatenate ALL bytes first, then interpret as PCM16)

If B's seam deltas are normal speech-range while A's are huge, the straddle is
real and the fix is a 1-byte carry in the backend reader.
"""
import asyncio
import json
import os
import struct
import time

import aiohttp

URL = os.getenv("PROBE_URL", "ws://localhost:8000/ws/call")
OUT = os.getenv("PROBE_OUT", "evidence/probe_frames")


async def capture():
    frames = []
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
                    frames.append(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
    return frames


def seam_deltas(samples, bounds, label):
    ds = []
    for i in bounds:
        if 0 < i < len(samples):
            ds.append(abs(samples[i] - samples[i - 1]))
    if not ds:
        print(f"{label}: no seams")
        return
    ds.sort()
    print(f"{label}: seams={len(ds)} max={ds[-1]} p95={ds[int(len(ds)*0.95)]} "
          f">8000: {sum(d > 8000 for d in ds)} >16000: {sum(d > 16000 for d in ds)}")


def main():
    frames = asyncio.run(capture())
    with open(OUT + ".bin", "wb") as f:
        for b in frames:
            f.write(b)
    print(f"captured {len(frames)} frames -> {OUT}.bin")

    odd = [(i, len(b)) for i, b in enumerate(frames) if len(b) % 2]
    print(f"odd-byte frames: {len(odd)} -> {odd[:12]}")

    # A) per-frame independent decode (current browser behavior)
    per_frame = []
    bounds_a = []
    for b in frames:
        n = len(b) // 2
        per_frame.extend(struct.unpack(f"<{n}h", b[:n * 2]))
        bounds_a.append(len(per_frame))
    seam_deltas(per_frame, bounds_a, "A) per-frame decode ")

    # B) continuous decode (whole byte stream as one PCM16 stream)
    stream = b"".join(frames)
    cont = list(struct.unpack(f"<{len(stream)//2}h", stream[:len(stream)//2*2]))
    bounds_b = []
    off = 0
    for b in frames:
        off += len(b)
        bounds_b.append(off // 2)
    seam_deltas(cont, bounds_b, "B) continuous decode")

    # C) per-frame decode WITH a 1-byte carry (the candidate fix)
    carried = []
    bounds_c = []
    carry = b""
    for b in frames:
        data = carry + b
        if len(data) % 2:
            carry = data[-1:]
            data = data[:-1]
        else:
            carry = b""
        n = len(data) // 2
        carried.extend(struct.unpack(f"<{n}h", data))
        bounds_c.append(len(carried))
    seam_deltas(carried, bounds_c, "C) carry decode     ")
    print(f"C total samples: {len(carried)} (continuous: {len(cont)}, per-frame: {len(per_frame)})")


if __name__ == "__main__":
    main()
