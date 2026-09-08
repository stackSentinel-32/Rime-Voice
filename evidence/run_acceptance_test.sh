#!/usr/bin/env bash
# One command a judge can run to reproduce the interruption acceptance test.
# Hermetic: no external services, no API keys (in-process fakeredis + SQLite).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements-dev.txt

echo "=============================================================="
echo " Rime Voice — Interruption Acceptance Test"
echo "   delay=${TOOL_CALL_DELAY_MS:-4000}ms  interrupt=${INTERRUPT_AT_MS:-1500}ms  threshold=${CANCEL_LATENCY_THRESHOLD_MS:-300}ms"
echo "=============================================================="

python -m pytest tests/ -v

echo ""
echo "--- Generating evidence artifacts from a real orchestrator run ---"
TOOL_CALL_DELAY_MS="${TOOL_CALL_DELAY_MS:-4000}" \
INTERRUPT_AT_MS="${INTERRUPT_AT_MS:-1500}" \
WARM_RUNS="${WARM_RUNS:-4}" \
  python evidence/collect_evidence.py

echo ""
echo "Wrote evidence/event_log_sample.json and evidence/latency_summary.json"
