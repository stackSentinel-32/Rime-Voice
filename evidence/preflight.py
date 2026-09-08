#!/usr/bin/env python3
"""Secret + configuration preflight check (run before every demo / at submission).

Validates in two layers:

  1. OFFLINE — env presence and placeholder detection for every required/optional var.
  2. LIVE (when keys are present) — actual API calls:
       Rime     POST https://users.rime.ai/v1/rime-tts  (synthesizes a short utterance
                with the EXACT model/speaker/lang triple from env — one call proves
                key + model + speaker + lang are all valid together)
       Gemini   GET  generativelanguage.googleapis.com models list, asserts GEMINI_MODEL
                is a live model id
       Deepgram GET  api.deepgram.com/v1/projects (auth check)

Pure stdlib (urllib) — runs in any venv, container, or CI without installs.
Exit code 0 only if all REQUIRED checks pass.

Usage:
  python evidence/preflight.py            # full check (offline + live where keys exist)
  python evidence/preflight.py --offline  # skip network calls (env hygiene only)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------- env loading

REQUIRED = ["RIME_API_KEY", "RIME_MODEL", "RIME_SPEAKER", "RIME_LANG",
            "GEMINI_API_KEY", "GEMINI_MODEL"]
OPTIONAL = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
            "DEEPGRAM_API_KEY", "DEEPGRAM_MODEL", "REDIS_URL", "DATABASE_URL"]

_PLACEHOLDER_HINTS = ("your_", "changeme", "<", "xxx")


def _load_dotenv() -> dict:
    """Minimal .env reader (no dependency): KEY=VALUE lines, # comments ignored."""
    env = {}
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                v = v.split(" #")[0].strip().strip('"').strip("'")
                env[k.strip()] = v
    return env


def _val(dotenv: dict, key: str) -> str:
    return os.environ.get(key, dotenv.get(key, ""))


def _is_placeholder(v: str) -> bool:
    return (not v) or any(h in v.lower() for h in _PLACEHOLDER_HINTS)


# ---------------------------------------------------------------- live checks

def _http(method: str, url: str, *, headers=None, body=None, timeout=15):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def check_rime(key: str, model: str, speaker: str, lang: str) -> tuple[bool, str]:
    """Synthesize a tiny utterance with the exact configured triple."""
    try:
        status, data = _http(
            "POST", "https://users.rime.ai/v1/rime-tts",
            headers={"Authorization": f"Bearer {key}", "Accept": "audio/mpeg",
                     "Content-Type": "application/json"},
            body={"speaker": speaker, "text": "Preflight check.", "modelId": model, "lang": lang},
        )
        if status == 200 and len(data) > 0:
            return True, f"synthesis ok ({len(data)} bytes audio, model={model}, speaker={speaker}, lang={lang})"
        return False, f"HTTP {status}, {len(data)} bytes"
    except urllib.error.HTTPError as e:
        hint = {401: "invalid API key", 400: "bad request — model/speaker/lang triple likely invalid"
                }.get(e.code, f"HTTP {e.code}")
        return False, f"{hint} ({e.code})"
    except Exception as e:
        return False, f"network error: {e}"


def check_gemini(key: str, model: str) -> tuple[bool, str]:
    try:
        status, data = _http(
            "GET", "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
        )
        names = [m.get("name", "").removeprefix("models/")
                 for m in json.loads(data).get("models", [])]
        if model in names:
            return True, f"model id '{model}' is live in the catalog ({len(names)} models)"
        return False, f"model id '{model}' NOT in live catalog — nearest: " + \
                      ", ".join(sorted(n for n in names if model.split('-')[0] in n)[:5])
    except urllib.error.HTTPError as e:
        return False, f"API rejected key (HTTP {e.code})"
    except Exception as e:
        return False, f"network error: {e}"


def check_deepgram(key: str) -> tuple[bool, str]:
    try:
        status, _ = _http("GET", "https://api.deepgram.com/v1/projects",
                          headers={"Authorization": f"Token {key}"})
        return status == 200, "key valid"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} (401 = invalid key)"
    except Exception as e:
        return False, f"network error: {e}"


# ---------------------------------------------------------------- main

def main() -> int:
    offline_only = "--offline" in sys.argv
    dotenv = _load_dotenv()
    rows: list[tuple[str, str, str]] = []  # (name, status, detail)
    failures = 0
    warnings = 0

    def add(name, ok, detail, warn_only=False):
        nonlocal failures, warnings
        if ok:
            rows.append((name, "PASS", detail))
        elif warn_only:
            warnings += 1
            rows.append((name, "WARN", detail))
        else:
            failures += 1
            rows.append((name, "FAIL", detail))

    print("Rime Voice — preflight check" + (" (offline mode)" if offline_only else ""))
    print("=" * 74)

    # layer 1: env hygiene
    for k in REQUIRED:
        v = _val(dotenv, k)
        add(k, not _is_placeholder(v), "set" if v else "MISSING/placeholder")
    for k in OPTIONAL:
        v = _val(dotenv, k)
        if _is_placeholder(v):
            rows.append((k, "WARN", "not set — feature degraded, not fatal"))
            warnings += 1
        else:
            rows.append((k, "PASS", "set"))
    if _val(dotenv, "LIVEKIT_URL") and not _val(dotenv, "LIVEKIT_URL").startswith("wss://"):
        add("LIVEKIT_URL scheme", False, "must start with wss://", warn_only=True)

    # layer 2: live checks
    if not offline_only:
        rime_key, model = _val(dotenv, "RIME_API_KEY"), _val(dotenv, "RIME_MODEL")
        speaker, lang = _val(dotenv, "RIME_SPEAKER"), _val(dotenv, "RIME_LANG")
        if not _is_placeholder(rime_key):
            ok, d = check_rime(rime_key, model, speaker, lang)
            add("Rime live synthesis (model+speaker+lang triple)", ok, d)
        gem_key, gem_model = _val(dotenv, "GEMINI_API_KEY"), _val(dotenv, "GEMINI_MODEL")
        if not _is_placeholder(gem_key):
            ok, d = check_gemini(gem_key, gem_model)
            add("Gemini model catalog", ok, d)
        dg_key = _val(dotenv, "DEEPGRAM_API_KEY")
        if not _is_placeholder(dg_key):
            ok, d = check_deepgram(dg_key)
            add("Deepgram auth", ok, d)

    for name, status, detail in rows:
        print(f"  [{status:^4}] {name:<46} {detail}")
    print("=" * 74)
    print(f"{len(rows) - failures - warnings} pass · {warnings} warn · {failures} fail")
    if failures:
        print("RESULT: FAIL — fix the items above before the demo.")
        return 1
    print("RESULT: READY ✓ (warnings = degraded features, see README §Failure behavior)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
