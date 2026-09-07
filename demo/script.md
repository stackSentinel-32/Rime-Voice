# Demo Script — Rime Voice (5 minutes)

**Setup before stage (T-30 min):**
- [ ] `./evidence/run_acceptance_test.sh` passes locally
- [ ] Render deployment healthy (`/health` → `{"ok":true}`)
- [ ] Verify `RIME_MODEL`/`RIME_SPEAKER`/`LLM_MODEL` against live catalogs — set in env
- [ ] `/status` shows all pills green (LLM/STT/Rime configured)
- [ ] Agent worker running (`python -m agent.worker`)
- [ ] Backup: laptop docker stack + phone hotspot

---

## 1. The problem (30s) — why this isn't another voice demo

> "Every voice agent demo works — until you interrupt it. The agent finishes its sentence,
> reads a stale answer aloud, and sometimes even books the wrong thing. We built the agent
> that survives being interrupted **mid-tool-call**."

## 2. Live evidence dashboard (2 min) — zero keys, pure differentiator

Open the deployed URL. **Interruption Evidence tab is already the landing view.**

1. "This is our claim, quantified: audio stops in under 300 ms, stale results are
   discarded, DB stays correct."
2. Click **▶ Run interruption scenario**.
   - Narrate the timeline as it fires: *"Slow lookup in flight, tagged version 1. I interrupt
     at 300 ms — version bumps to 2, Rime audio cancelled — measured right there,
     sub-millisecond orchestration path. Fresh update dispatches tagged v2. Now watch:
     the stale v1 result comes back late… **discarded_stale** — never spoken, never written.
     The v2 result applies, and the database diff shows Friday only — no merge."*
3. Point at the three metric cards: **cancel latency / stale discarded / final state — all PASS.**
4. "Same code path as our published evidence file — the dashboard cannot lie."

If asked about real-world latency: *"STT barge-in detection adds a ~150 ms floor; it's
disclosed in our limitations, and orchestration adds near zero on top."*

## 3. Live voice call (1.5 min) — the wow moment

**Live Call tab → 🔊 Connect call** (mic permission → orb goes cyan "listening").

1. **Happy path:** "Check my booking 12." → agent answers via Rime TTS.
2. **The kill shot:** "Check booking 15." → **interrupt mid-sentence**: "—actually, Friday."
   → agent stops instantly, answers only the Friday outcome.
3. **Mid-tool-call:** ask for something slower, interrupt a beat later — "the tool call was
   still in flight; you heard it drop the old answer and never look back."

If live audio fails → fall back to step 2 on the dashboard (it is the full differentiator),
and say so plainly: "we're showing you the same logic hermetically; the failure was
transport/keys, not the claim."

## 4. Architecture (45s)

> "Everything hangs on Redis-backed turn-versioning: every tool dispatch is tagged with the
> version at dispatch; interruptions bump the version; results are compared on arrival and
> stale ones are discarded before they can be spoken or committed. Writes are split into
> validate + commit so a stale call can't even reach Postgres. Same logic runs hermetic in
> tests and live in the worker."

## 5. Close (15s)

> "Interruption handling isn't prompt polish — it's a distributed-state problem, and we solved
> it with a mechanism, a test, and a measured number. Reproduce it yourself:
> one command, `./evidence/run_acceptance_test.sh`."

---

## Judge Q&A prep

- **"Is the <300 ms measured end-to-end?"** — No, and we say so: orchestration path is
  sub-ms (measured); STT barge-in floor is ~150 ms (disclosed); end-to-end adds transport.
  The invariant test isolates exactly the part we own.
- **"What if Rime can't cancel mid-stream server-side?"** — Wrapper probes native cancel;
  fallback is client-side buffer drop; the versioning guarantee (no stale speech/writes)
  holds regardless. Status disclosed in RIME_EVIDENCE.md.
- **"Is the data real?"** — Synthetic, deterministic, 40 rows. No real PII, ever.
- **"What breaks under rapid double-interrupts?"** — Nothing: monotonicity is unit-tested
  (sequential, concurrent, and rapid double-interrupt scenarios all pass).
