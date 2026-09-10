# Demo Video Script — Rime Voice: Interruption-Hardened Booking Agent

**Target length: 5:00–5:45.** Narration ≈ 130–140 words/min → keep spoken lines ≈ 700–800 words total.
Every fact below is taken from the actual code and the measured evidence in `RIME_EVIDENCE.md` —
do not ad-lib numbers you have not seen on screen.

---

## 0. Recording checklist (do this before you press record)

| Item | How |
|---|---|
| Terminal (dark theme, big font) | `python -m pytest tests/ -v` and `python evidence/collect_evidence.py` (Windows) — or `./evidence/run_acceptance_test.sh` in Git Bash / WSL. Both print the same PASS. |
| Browser | `docker compose up --build`, open `http://localhost:8000` → **Interruption Evidence** tab. |
| Code editor | Pre-open `agent/turn_manager.py`, `redis_state/session_store.py`, `agent/demo_scenario.py`. |
| Provider status | `http://localhost:8000/status` — this is the "which speech provider is active" proof point. |
| Mic | Record narration in a second pass over the screen capture (OBS: separate audio track). Easier to keep timing clean. |
| Honesty rule | If you have no API keys, you show the **hermetic path only** and say so out loud (Scene 8). Never imply the audio you hear is live Rime when it is a stand-in. |

**Scene timing cheat-sheet**

| # | Time | Beat |
|---|---|---|
| 1 | 0:00–0:50 | User, problem, why removing voice kills the product |
| 2 | 0:50–1:35 | Normal end-to-end flow + Rime configuration + `/status` |
| 3 | 1:35–2:25 | The hard voice problem + the mechanism (turn-versioning, plain English) |
| 4 | 2:25–3:15 | Stress case: run the interruption scenario, watch the timeline, verdict |
| 5 | 3:15–4:05 | Repo walkthrough — every module & directory, easy words |
| 6 | 4:05–4:45 | Results + evidence artifacts + limitations disclosed |
| 7 | 4:45–5:20 | (Optional, only with keys) live call — else go straight to 8 |
| 8 | 5:20–5:45 | Provider statement + close |

---

## Scene 1 — 0:00–0:50 · The user, the problem, why it has to be voice

**On screen:** README's one-slide pitch diagram (user utterance → interrupt → version bump → cancel → discard → commit).

**Narration (~110 words):**

> "Meet Riya. She's at a clinic front desk, phone to her ear, rescheduling a patient's booking while the agent on the line is still talking.
>
> Voice agents fail here all the time. You interrupt to change your mind, and the agent keeps talking over you — then it silently acts on the *old* request. Thursday gets changed to Friday, but the booking is updated for Thursday anyway, because that stale result was applied after you spoke.
>
> That race — *new instruction arriving while old work is still in flight* — is the hardest bug in voice products, and it's the one we chose to prove. Our agent is a booking assistant whose entire job is getting this race right.
>
> Remove the voice from this product and there's nothing left: the interruption problem only exists because speech is real-time. Voice is the product."

**Caption overlay:** "Hard voice problem: **Interruption & recovery** — stop old audio, discard stale tool results, respond to only the latest request."

---

## Scene 2 — 0:50–1:35 · Normal end-to-end flow + exact Rime config

**On screen:** browser → `/status`; then the header pills; then code of `agent/worker.py` `_livekit_components()`.

**Narration (~105 words):**

> "Here's the normal call. Riya says, *'Check booking 12.'* Deepgram transcribes her mid-speech — not waiting for silence, because speed matters. Gemini reads the transcript and decides: call the `lookup_booking` tool. The booking backend looks up the row. Then the answer is spoken back by Rime — the voice that is the whole point of this product.
>
> The status page shows the exact active speech configuration: model `mistv3`, speaker `cove`, language `en` — chosen for the lowest first-audio latency in Rime's current catalog. Rime streams over a secure WebSocket, MP3 audio at 24 kHz. And when any provider is missing, the page tells the truth — it never fakes a green light.
>
> Happy path is easy. Every voice agent can do this. The hard part is what happens next."

**Caption overlay (exact config, ~4 s hold):** `RIME_MODEL=mistv3 · RIME_SPEAKER=cove · RIME_LANG=en · wss://users-ws.rime.ai/ws3 · mp3 · 24000 Hz`.

---

## Scene 3 — 1:35–2:25 · The hard problem and the mechanism, in plain English

**On screen:** `agent/turn_manager.py` and `redis_state/session_store.py`, line highlights on `increment_turn_version`, `discarded_stale`, `pop_pending_tool`.

**Narration (~115 words):**

> "The problem: a tool call takes real time. Ours is deliberately slowed to four seconds so we can prove the race. Meanwhile the user keeps talking. So how do we know, when the slow answer finally arrives, whether it's still what the user wants?
>
> Our trick is *turn-versioning*. Think of a numbered ticket.
>
> Every request gets the current ticket number stamped on it. When the user interrupts — mid-speech or mid-tool-call — we tear up the old tickets and issue a new number. Queued Rime audio is cancelled immediately. When a slow result comes back, we check its ticket: *old ticket, current number?* If not, it is logged as `discarded_stale` — never spoken, never applied.
>
> The ticket numbers live in Redis, per session, as an atomic counter — so even two interruptions in the same second can't collide. And mutating tools are split into *validate* and *commit*: a stale call may compute a proposal, but only the non-stale path is ever allowed to write to the database."

**Caption overlay:** "tag every dispatch → bump version on interrupt → stale tag ≠ current version ⇒ `discarded_stale`."

---

## Scene 4 — 2:25–3:15 · The stress case, live on the dashboard

**On screen:** browser → `http://localhost:8000`, click **⏱ Exact roadmap timing (4s / 1.5s)**. Let the timeline animate to completion.

**Narration (~120 words):**

> "Now the acceptance test we wrote *before* building — exact roadmap timings. A tool call with four seconds of artificial delay is dispatched. One and a half seconds in, the user interrupts and changes the request to Friday.
>
> Watch the timeline fire in order: dispatch tagged version one… `user_speech_start_interrupt`… `tts_cancel`… and here's the number that matters — cancel latency under a millisecond, against a 300-millisecond threshold. Then, four seconds after it started, the *original* lookup finally crawls back. Version one. Current version two. The verdict: `discarded_stale` — thrown away, never spoken. Meanwhile the fresh Friday update is applied.
>
> And the database proves it: *before* on the left, *after* on the right. The date moved to Friday. Not a merge, not a corrupted row — only the latest request. PASS."

**Caption overlay:** `interrupt @ 1.5 s · cancel < 300 ms · stale 1 discarded · fresh 1 applied · final state = Friday ✓`.

---

## Scene 5 — 3:15–4:05 · Repo walkthrough — every module in easy words

**On screen:** editor file tree, open each file as named. Keep each to one or two sentences.

**Narration (~140 words):**

> "Here's the whole repo — small enough to read in one sitting.
>
> `agent/` is the brain. `turn_manager.py` is the referee — it stamps tickets, compares them on arrival, and decides stale versus fresh. `demo_scenario.py` is the scripted interruption scene; the dashboard button and the evidence file both run this *same* code, so the demo can never lie about the repo. `event_log.py` is the black-box recorder — every decision becomes a timestamped JSON event. `stt_pipeline.py` is the ear and the interruption detector. `tts_rime.py` wraps the voice with cancellation — native stop if the plugin supports it, otherwise it stops forwarding audio chunks. `llm_tools.py` lets Gemini pick the tool. `worker.py` is the only file that touches LiveKit.
>
> `backend/` is the clinic's booking system — FastAPI plus a database. `tools.py` is where the write-safety lives: validate computes, commit persists, and only fresh results reach commit. `redis_state/session_store.py` holds the ticket numbers. `web/index.html` is the whole UI, one file. `tests/` is the hermetic suite — it swaps in a fake TTS and fake Redis, so the race is provable with zero keys. `evidence/` is the acceptance harness and the receipts."

---

## Scene 6 — 4:05–4:45 · Numbers, artifacts, and honest limitations

**On screen:** terminal output of `python evidence/collect_evidence.py`; then open `evidence/latency_summary.json` and `evidence/event_log_sample.json`.

**Narration (~110 words):**

> "Reproducibility: one command regenerates everything. Cold run, four warm runs — every cancellation is under the 300-millisecond threshold, one stale result discarded, one applied, final state correct every run. The full event log is committed to the repository.
>
> Now the honest part — what this measures and what it doesn't. The sub-millisecond number is the *orchestration* cancel path: version check plus flag flip. In the real product, audio also has to stop across the network, and interruption can't even be detected until speech recognition surfaces a partial transcript — a floor of roughly 150 milliseconds. That's disclosed, not hidden.
>
> And the load-bearing caveat: Rime's mid-stream cancellation is implemented with a native-stop probe and a buffer-drop fallback, and it is unit-tested — but the live plugin path still has to be confirmed with real keys before submission. We verify that with the preflight script, which synthesizes one real utterance with our exact model, speaker, and language."

**Caption overlay:** "Measured: orchestration cancel 0.012 ms cold / 0.013–0.017 ms warm. Not measured: network + STT floor (~150 ms)."

---

## Scene 7 — 4:45–5:20 · OPTIONAL (only if live keys are configured)

**On screen:** browser live-call tab → Connect → real interruption over WebRTC; then `python evidence/preflight.py` showing `[PASS] Rime live synthesis`.

**Narration (~90 words):**

> "And with real credentials, the same scenario over an actual call: Riya connects from the browser over WebRTC. She interrupts mid-answer — the live orb shows the agent stop speaking promptly, and the follow-up confirms only the corrected booking.
>
> The preflight script proves the shipped path: one real synthesis call with model `mistv3`, speaker `cove`, language `en` — key, model, speaker, and language all validated together."

**If you do NOT have keys, skip this scene entirely** and fold this sentence into Scene 8:

> "We're running the hermetic path today — every component that touches a live API reports *not configured* honestly rather than faking success, which is exactly the failure behavior we ship."

---

## Scene 8 — 5:20–5:45 · Provider statement + close

**On screen:** `/status` page (or the pills); then the README pitch diagram again, frozen.

**Narration (~85 words):**

> "So, to be explicit about the stack: the primary spoken output is **Rime** — model `mistv3`, speaker `cove`, English, streamed over the Rime WebSocket as MP3 at 24 kilohertz. Rime is not a play button on a chatbot; it is the channel the whole interruption problem lives in.
>
> Everything else is ours: Deepgram for streaming speech recognition, Gemini for tool decisions, LiveKit for transport, Redis for the ticket numbers, and a booking backend that can only be written to by fresh results.
>
> One voice-native problem, one mechanism, one measurable acceptance test — interruption, tamed. Thank you."

**End card:** repo URL, `RIME_EVIDENCE.md`, `./evidence/run_acceptance_test.sh`.

---

## Plain-English cheat sheet (for you, and for judge Q&A)

| Module / dir | One-line job | Analogy |
|---|---|---|
| `agent/turn_manager.py` | Tags every tool call with the turn version; on result arrival, stale ⇒ discard, fresh ⇒ apply + speak. | The referee with the whistle. |
| `redis_state/session_store.py` | Redis-backed per-call state: turn counter, pending-tool tags, confirmed state. | The scoreboard (atomic, so two interrupts can't collide). |
| `agent/stt_pipeline.py` | VAD/STT seam — decides *is this barge-in or a new turn?*; noise never bumps the version. | The ear + the "is she interrupting?" judge. |
| `agent/tts_rime.py` | Rime voice wrapper with cancellation (native stop probe, buffer-drop fallback). | The mouth that can shut up on command. |
| `agent/llm_tools.py` | Gemini transcript → `{tool, args}` decision. Deliberately *not* the differentiator. | The brain that picks the action. |
| `agent/tool_client.py` | Local (in-process) or HTTP tool runner; injects delay to reproduce the race. | The messenger. |
| `agent/event_log.py` | Monotonic-ms structured JSON event log. | The black-box recorder. |
| `agent/demo_scenario.py` | One shared scripted scenario used by evidence + dashboard. | One truth, two screens. |
| `agent/worker.py` | LiveKit composition root; only file that imports livekit. | The power plug. |
| `backend/tools.py` | Booking logic as pure functions; **validate vs commit split** = stale writes impossible. | The bank teller who can't sign until approved. |
| `backend/main.py` | FastAPI: tool endpoints, `/token`, `/demo/run`, `/status`, mounts the UI. | The front desk / HTTP door. |
| `backend/db.py`, `models.py`, `seed.py` | SQLAlchemy, Postgres/SQLite, 40 synthetic rows (seed 42). | The filing cabinet (fake patient files). |
| `web/index.html` | Single-file SPA: evidence dashboard + live-call orb. | The control room. |
| `tests/` | Hermetic suite: fake TTS + fakeredis prove the invariants with no keys. | The crash-test lab. |
| `evidence/` | Acceptance runner, artifact collector, secret/config preflight. | The receipts + the pre-flight check. |

**The three invariants (memorize these):**
1. `tts_cancel` latency < 300 ms from interruption detection.
2. No tool result whose tagged version ≠ current version is ever `applied` (or spoken).
3. Final DB state reflects only the latest request — never a merge.

**Likely judge questions + one-line answers**

- *Why Redis?* The counter must be atomic under rapid double-interrupts; and it makes the state real across a distributed app, not a Python global.
- *Why is the cancel so fast?* The orchestration cancel is a version check + flag flip in-process — sub-ms by construction. Audio across the network adds the STT floor (~150 ms), which we disclose.
- *Is that Rime audio I'm hearing?* Only in the live-call scene. The hermetic demo uses a stand-in and we say so — the real Rime path is verified by `preflight.py` with one live synthesis.
- *What happens if a provider dies?* No fake success: `/status` shows it, live endpoints 503 with a clear reason, and turn-versioning keeps working because it doesn't depend on the LLM.
- *Where's the acceptance test defined?* `RIME_EVIDENCE.md` + `tests/test_interruption.py` — 4 s tool delay, interrupt at 1.5 s, threshold 300 ms, and a write-variant test proves a stale *cancel* never reaches the DB.

## Editing notes

- Cut any scene that runs over budget; keep Scenes 1, 4, 6, 8 mandatory (problem / stress case / evidence / provider).
- Add captions for every number you quote — judges watch muted and read your captions.
- Do **not** loop ambient "demo music"; keep Rime/agent audio clean in the mix.
