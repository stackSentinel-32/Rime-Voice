/* AudioWorklet processors — all audio conversion on the render thread.

   pcm-capture: mic Float32 (context rate) -> resample to 16 kHz -> Int16 batches
   pcm-player : raw PCM16 @ 24 kHz from main thread -> convert + resample -> gapless out

   Break-elimination rules (learned the hard way):
   1. The pre-buffer (~250ms) applies ONLY at utterance start. Once started, we play
      through any hiccup — an underrun produces at most a few ms of silence, never a
      re-buffer pause. (Re-requiring the full buffer after an underrun was the
      mid-sentence "break".)
   2. Underruns only GROW the start-buffer for the NEXT utterance (flush resets).
   3. End of speech is server-driven (flush) — never fade or reset on starvation. */
"use strict";

const FADE = 96; // ~2ms soft fade-in at utterance start — kills the start click

function resampleTo(src, srcRate, dstRate) {
  if (srcRate === dstRate) return src;
  const ratio = srcRate / dstRate;
  const out = new Float32Array(Math.max(1, Math.ceil(src.length * dstRate / srcRate)));
  for (let i = 0; i < out.length; i++) {
    const pos = i * ratio;
    const i0 = Math.min(Math.floor(pos), src.length - 1);
    const i1 = Math.min(i0 + 1, src.length - 1);
    const frac = pos - i0;
    out[i] = src[i0] * (1 - frac) + src[i1] * frac;
  }
  return out;
}

class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.batch = new Float32Array(1024);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.batch[this.n++] = ch[i];
      if (this.n === this.batch.length) {
        const f16 = resampleTo(this.batch, sampleRate, 16000);
        const int16 = new Int16Array(f16.length);
        for (let j = 0; j < f16.length; j++) {
          int16[j] = Math.max(-1, Math.min(1, f16[j])) * 32767 | 0;
        }
        this.port.postMessage(int16.buffer, [int16.buffer]);
        this.n = 0;
      }
    }
    return true;
  }
}

class PCMPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.pos = 0;
    this.started = false;      // latched for the whole utterance (until flush)
    this.fadeIn = 0;
    this.preBuffer = 0.25;     // seconds; start-of-utterance only
    this.port.onmessage = (e) => {
      if (e.data && e.data.flush) {
        this.queue = []; this.pos = 0; this.started = false; this.fadeIn = 0;
        return;
      }
      if (!(e.data instanceof ArrayBuffer)) return;
      const dv = new DataView(e.data);
      const pcm = new Float32Array(dv.byteLength / 2);
      for (let i = 0, j = 0; j + 1 < dv.byteLength; i++, j += 2) {
        pcm[i] = dv.getInt16(j, true) / 32768;
      }
      const out = resampleTo(pcm, 24000, sampleRate);
      if (out.length) this.queue.push(out);
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!this.started) {
      let queued = 0;
      for (const c of this.queue) queued += c.length;
      if (queued < sampleRate * this.preBuffer) {
        out.fill(0);
        return true;
      }
      this.started = true;     // latched: no re-buffering until the next flush
      this.fadeIn = FADE;
      this.preBuffer = Math.min(0.6, this.preBuffer);  // keep for next start
    }
    let starved = false;
    for (let i = 0; i < out.length; i++) {
      while (this.queue.length && this.pos >= this.queue[0].length) {
        this.queue.shift(); this.pos = 0;
      }
      if (!this.queue.length) { starved = true; out[i] = 0; continue; }
      let s = this.queue[0][this.pos++];
      if (this.fadeIn > 0) s *= 1 - (this.fadeIn-- / FADE);
      out[i] = s;
    }
    if (starved) {
      // next utterance starts with a bigger cushion; THIS one keeps playing
      this.preBuffer = Math.min(0.6, this.preBuffer * 1.5);
    }
    return true;
  }
}

registerProcessor("pcm-capture", PCMCaptureProcessor);
registerProcessor("pcm-player", PCMPlayerProcessor);
