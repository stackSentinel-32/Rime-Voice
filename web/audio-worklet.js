/* AudioWorklet processors — all audio conversion on the render thread.

   pcm-capture: mic Float32 (context rate) -> resample to 16 kHz -> Int16 batches
   pcm-player : raw PCM16 @ 24 kHz from main thread -> convert + resample -> gapless out

   Break-elimination rules (measured against live Rime, 2026-09-10 —
   evidence/probe_rime_cadence.py: audio bursts in ~1-2s for many seconds of
   speech, but the first ~10% trickles with ~300ms inter-chunk holes):
   1. The start cushion (500ms) must exceed the worst measured start hole
      (~320ms). It applies ONLY at utterance start. An escape hatch starts
      playback anyway if audio has been trickling >1.2s without ever filling
      the cushion (pathological-slow path: waiting gains nothing there).
   2. Once started, we play THROUGH hiccups: an underrun costs at most a few
      ms of faded silence, never a re-buffer pause mid-sentence. Underruns
      grow the start cushion for the NEXT utterance (flush keeps the grown
      value).
   3. Starvation boundaries fade out/in over ~1ms — an underrun sounds like a
      breath, not a click.
   4. End of speech is server-driven (flush) — never fade or reset on starvation. */
"use strict";

const START_FADE = 96;    // ~2ms soft fade-in at utterance start — kills the start click
const STARVE_FADE = 48;   // ~1ms fade at underrun boundaries — kills resume clicks
const CUSHION_MAX = 0.8;  // seconds; cap for the adaptive growth in rule 2

function resampleCubic(src, srcRate, dstRate) {
  if (srcRate === dstRate) return src;
  const ratio = srcRate / dstRate;
  const out = new Float32Array(Math.max(1, Math.ceil(src.length * dstRate / srcRate)));
  for (let i = 0; i < out.length; i++) {
    const pos = i * ratio;
    const i1 = Math.min(Math.floor(pos), src.length - 1);
    const i0 = Math.max(i1 - 1, 0);
    const i2 = Math.min(i1 + 1, src.length - 1);
    const i3 = Math.min(i1 + 2, src.length - 1);
    const t = pos - i1;
    // Catmull-Rom: no kinks at chunk edges (linear interpolation audibly
    // "roughens" 24kHz speech on a 48kHz context)
    const a = -0.5 * src[i0] + 1.5 * src[i1] - 1.5 * src[i2] + 0.5 * src[i3];
    const b = src[i0] - 2.5 * src[i1] + 2.0 * src[i2] - 0.5 * src[i3];
    const c = -0.5 * src[i0] + 0.5 * src[i2];
    out[i] = ((a * t + b) * t + c) * t + src[i1];
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
        const f16 = resampleCubic(this.batch, sampleRate, 16000);
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
    this.fadeIn = 0;           // remaining fade-in samples
    this.fadeInLen = 1;        // total length of the current fade-in ramp
    this.fadeOut = 0;          // counts up while starving (fade-out ramp)
    this.wasStarved = false;   // first sample after a dry spell fades back in
    this.last = 0;             // last written sample: anchor for the fade-out
    this.reportedUnderrun = false;  // one report per utterance
    this.firstChunkAt = null;  // currentTime of first audio (escape-hatch clock)
    this.preBuffer = 0.5;      // seconds; start-of-utterance only
    this.port.onmessage = (e) => {
      if (e.data && e.data.flush) {
        this.queue = []; this.pos = 0; this.started = false; this.fadeIn = 0;
        this.fadeOut = 0; this.wasStarved = false; this.last = 0;
        this.reportedUnderrun = false; this.firstChunkAt = null;
        return;
      }
      if (!(e.data instanceof ArrayBuffer)) return;
      if (this.firstChunkAt === null) this.firstChunkAt = currentTime;
      const dv = new DataView(e.data);
      const pcm = new Float32Array(dv.byteLength / 2);
      for (let i = 0, j = 0; j + 1 < dv.byteLength; i++, j += 2) {
        pcm[i] = dv.getInt16(j, true) / 32768;
      }
      const out = resampleCubic(pcm, 24000, sampleRate);
      if (out.length) this.queue.push(out);
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!this.started) {
      let queued = 0;
      for (const c of this.queue) queued += c.length;
      const target = sampleRate * this.preBuffer;
      // rule 1: full cushion, or the escape hatch (trickling >1.2s, never filled)
      const escape = this.firstChunkAt !== null
        && (currentTime - this.firstChunkAt) > 1.2 && queued > 0.1 * sampleRate;
      if (queued < target && !escape) {
        out.fill(0);
        return true;
      }
      this.started = true;     // latched: no re-buffering until the next flush
      this.fadeIn = START_FADE;
      this.fadeInLen = START_FADE;
    }
    let starved = false;
    for (let i = 0; i < out.length; i++) {
      while (this.queue.length && this.pos >= this.queue[0].length) {
        this.queue.shift(); this.pos = 0;
      }
      if (!this.queue.length) {
        starved = true;
        this.wasStarved = true;
        // rule 3: fade the LAST speech sample down instead of a hard cut to zero
        const g = this.fadeOut < STARVE_FADE ? 1 - this.fadeOut++ / STARVE_FADE : 0;
        out[i] = this.last * g;
        continue;
      }
      if (this.wasStarved) {   // rule 3: fade back in on resume
        this.wasStarved = false;
        this.fadeOut = 0;
        this.fadeIn = STARVE_FADE;
        this.fadeInLen = STARVE_FADE;
      }
      let s = this.queue[0][this.pos++];
      if (this.fadeIn > 0) s *= 1 - (this.fadeIn-- / this.fadeInLen);
      this.last = s;
      out[i] = s;
    }
    if (starved) {
      // rule 2: next utterance starts with a bigger cushion; THIS one keeps playing
      this.preBuffer = Math.min(CUSHION_MAX, this.preBuffer * 1.25);
      if (!this.reportedUnderrun) {   // surface once per utterance for debugging
        this.reportedUnderrun = true;
        this.port.postMessage({underrun: true, cushion_ms: Math.round(this.preBuffer * 1000)});
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PCMCaptureProcessor);
registerProcessor("pcm-player", PCMPlayerProcessor);
