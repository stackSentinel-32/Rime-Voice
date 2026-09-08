# Rime Voice — Interruption-Hardened Voice Booking Agent

A voice-native booking/support agent whose differentiator is **correct interruption handling**:
when the user interrupts the agent mid-speech *or mid-tool-call*, queued Rime audio stops,
the stale tool result is discarded (never spoken, never written), and the agent responds to
**only the latest request**.

Built to the roadmap in `rime-hackathon-roadmap.pdf`. **Proof lives in [`RIME_EVIDENCE.md`](RIME_EVIDENCE.md)** — every number is measured, not narrated.

## The one-slide pitch

```
user: "Check my booking for Thursday"     ──┐
agent: (tool call in flight, speaking…)     │ user interrupts 1500ms in
user: "—actually, make it Friday"        ──┘
                                            │
   turn_version: v1 ──► v2                  │  Redis-backed
   queued Rime audio: cancelled (<300ms)    │  turn-versioning
   stale v1 result: discarded_stale ✗       │  (the core)
   fresh v2 result: applied, committed ✓    │
   agent speaks ONLY the Friday outcome     │
```

## Quickstart (60 seconds, zero API keys)

```bash
./evidence/run_acceptance_test.sh     # hermetic acceptance test: 14 tests + evidence artifacts
docker compose up --build             # full stack: UI + dashboard + Postgres + Redis
open http://localhost:8000            # → "Run interruption scenario"
```

The dashboard replays the acceptance scenario against the **real orchestrator and real
database** — you watch the event timeline fire live (dispatch → interrupt → `tts_cancel`
latency → `discarded_stale` → applied → final DB state) with measured latencies. No API keys
needed for this part; it demos the entire differentiator on its own.

## What's real vs what needs keys

| Component | Status |
|---|---|
| Turn-versioning core (`agent/turn_manager.py`) | **Real, tested** — 14 automated tests |
| Booking backend + tools (FastAPI + Postgres/SQLite) | **Real, running** — synthetic data only |
| Redis session state (or in-process fakeredis) | **Real, running** |
| Evidence dashboard + event log (§5.4 schema) | **Real, running** |
| LiveKit worker / Rime TTS / Deepgram STT / LLM loop | **Code complete, needs API keys** (`agent/worker.py`) |
| Rime mid-stream cancel | Wrapper + fallback implemented & unit-tested; **native cancel must be verified against live Rime** (Phase 0) — see RIME_EVIDENCE.md §Limitations |

Honest-labeling policy: `/status` and the UI header always show which providers are live.
Missing keys degrade to clear 503s, never fake success.

## Running the full voice stack

```bash
cp .env.example .env   # fill in LIVEKIT_*, RIME_*, DEEPGRAM_*, GEMINI_*
python evidence/preflight.py          # secret + config preflight (live API checks)

# 1. infra + UI + dashboard
docker compose up --build

# 2. agent worker (joins LiveKit rooms; publishes Rime audio)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m agent.worker

# 3. open http://localhost:8000 → "Live Call" → Connect
```

Open [http://localhost:8000/status](http://localhost:8000/status) to confirm providers.

## Exact Rime configuration

All values verified against docs.rime.ai on **2026-09-08**. Re-verify at submission time
with `python evidence/preflight.py` (which synthesizes a real utterance with this exact
triple — one call proves key + model + speaker + lang together):

| Setting | Value | Notes |
|---|---|---|
| Model ID | `mistv3` | Fastest current model (~37 ms P50 TTFB); `coda` is the flagship-quality alternative |
| Speaker | `cove` | Mist v3–only voice (female, young adult); other confirmed starters: `astra`, `luna` |
| Language | `en` | BCP-47 two-letter code; three-letter `eng` is legacy-compat only |
| Endpoint | `wss://users-ws.rime.ai/ws3` | JSON streaming WebSocket (recommended; `/ws2` and `/ws` are legacy) |
| Auth | `Authorization: Bearer <RIME_API_KEY>` | Connection header — **server-side only** (browser WS API can't set headers) |
| Audio format | `mp3` (`audioFormat` query param) | Also `wav`, `ogg`, `webm`, `pcm`/`l16`, `mulaw` |
| Sampling rate | `24000` Hz (`samplingRate` query param) | Rime default; values above 24 kHz are upsampled |
| Transport | WebSocket query params + JSON messages | Synthesis args on the connection URL; audio streams back as base64 JSON chunks |
| LLM (tool calling) | Gemini `gemini-3.8-flash` | GA, agent-tuned; via `google-genai` SDK / `livekit-plugins-google` |

⚠️ Requests that omit `modelId` are served by Mist v3, **not** the expected default —
we always set it explicitly. Unset/invalid keys degrade to 503s (see Failure behavior).

## Third-party services

| Service | Role | Key env vars | Required for |
|---|---|---|---|
| Rime (users.rime.ai) | TTS — the voice | `RIME_API_KEY`, `RIME_MODEL`, `RIME_SPEAKER`, `RIME_LANG` | Live call audio |
| Google Gemini (generativelanguage.googleapis.com) | Tool-calling LLM | `GEMINI_API_KEY`, `GEMINI_MODEL` | Intent→tool decisions in live calls |
| Deepgram | Streaming STT (interim results) | `DEEPGRAM_API_KEY`, `DEEPGRAM_MODEL` | Barge-in transcription |
| LiveKit Cloud | Room transport, VAD, WebRTC | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | `/token` + live call tab |
| Redis | Turn-version state store | `REDIS_URL` | Full app (fakeredis fallback in tests) |
| PostgreSQL | Booking records | `DATABASE_URL` | Full app (SQLite fallback) |

## Failure behavior

No component ever fakes success — missing credentials degrade loudly and specifically:

| What's missing | Behavior |
|---|---|
| No keys at all (Render default) | App boots; dashboard + evidence run fully (hermetic path); `/token` → **503** "not configured"; UI pills show "not configured" |
| Bad/invalid Rime key or model/speaker triple | `evidence/preflight.py` FAILs with the HTTP code (401 = key, 400 = triple); live call won't produce audio |
| Gemini key missing | Worker logs `llm_unavailable` per utterance (structured event, visible in logs); **turn-versioning still works** — interruption handling is LLM-independent |
| Gemini API error mid-call | Worker logs `llm_error` and continues the callback loop; session survives |
| Redis/Postgres down | Compose healthchecks block backend start; hermetic test path unaffected (fakeredis/SQLite) |
| LiveKit room unreachable | Worker exits with LiveKit's error; dashboard unaffected |

## Known limitations

- **STT barge-in floor (~150 ms):** interruption can't be detected before STT surfaces a
  partial transcript; this is additive to our sub-ms orchestration cancel path. Disclosed
  in `RIME_EVIDENCE.md` §Limitations.
- **Rime native mid-stream cancel unverified live:** wrapper probes for native cancel with
  a client-side buffer-drop fallback; unit-tested, but the live-plugin mechanism hasn't
  been confirmed with real keys yet (recorded in RIME_EVIDENCE.md at verification time).
- **Hermetic vs end-to-end latency:** the acceptance test measures the orchestration path
  (version check + cancel + discard + commit gating), not network/WebRTC audio drain.
- **`coda` quality option:** `mistv3` chosen for lowest TTFB; swap `RIME_MODEL=coda` +
  a Coda voice (e.g. `luna`) if a judge prefers quality over speed.
- **Synthetic data only** — deterministic seed (42), 40 rows, no real PII.

## Deploy to Render

`render.yaml` deploys one free web service: UI + dashboard + tools + LiveKit token endpoint
on SQLite, boots with zero secrets. Add secrets in the Render dashboard (LIVEKIT_*,
RIME_*, DEEPGRAM_*, GEMINI_*, optional REDIS_URL/DATABASE_URL) to level up.

## Repo map

```
agent/
  turn_manager.py     ← THE core: dispatch tagging, version compare, stale discard, commit gating
  tts_rime.py         Rime wrapper: native cancel probe + buffer-drop fallback
  stt_pipeline.py     InterruptionDetector (VAD/STT → on_interrupt), noise gate
  llm_tools.py        Gemini tool-calling via google-genai (env-switchable model)
  worker.py           LiveKit composition root (heavy imports deferred)
  demo_scenario.py    shared acceptance scenario (dashboard + evidence, one path)
  event_log.py        §5.4 structured JSON events, monotonic ms
  tool_client.py      LocalToolClient (in-process) | HttpToolClient (FastAPI)
backend/
  main.py             FastAPI: tools + /token + /demo/run + UI mount
  tools.py            lookup / validate / commit — writes only commit when non-stale
  db.py models.py     SQLAlchemy (Postgres | SQLite), synthetic bookings
  seed.py             40 deterministic synthetic rows (seed 42)
redis_state/
  session_store.py    turn_version, pending_tools, confirmed state (redis | fakeredis)
web/index.html        Dark-theme SPA: evidence dashboard + live-call orb UI
tests/                14 tests: 3 invariants, stale-write, monotonicity, cancel wrapper
evidence/             run_acceptance_test.sh + collect_evidence.py + artifacts
```

## Testing & evidence

```bash
./evidence/run_acceptance_test.sh                            # full: 14 tests + artifacts
TOOL_CALL_DELAY_MS=800 INTERRUPT_AT_MS=300 ./evidence/run_acceptance_test.sh  # fast
python evidence/collect_evidence.py                          # regenerate evidence only
```

Three invariants asserted (details in `RIME_EVIDENCE.md`):
1. `tts_cancel` latency < 300 ms from interrupt detection
2. No stale tool result is ever applied (or spoken)
3. Final DB state reflects only the latest request — never a merge

## Security

- No real customer data — synthetic seed only
- `.env` git-ignored; secrets never appear in code, docs, or logs
- LiveKit tokens are minted server-side with scoped grants (`/token`)
