"""Gemini LLM decision path (agent/llm_tools.py) — hermetic, no network, no key, no SDK.

We stub both the client AND the google.genai.types module: the unit under test is OUR
code (schema wiring, function-call extraction, fallback behavior), not Google's SDK.
Live key/model validation is covered by evidence/preflight.py instead.
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

import agent.llm_tools as L


@pytest.fixture
def stub_genai(monkeypatch):
    """Inject a minimal google.genai.types so decide_tool works without the real SDK."""
    types_mod = ModuleType("google.genai.types")

    class _Cfg:
        def __init__(self, system_instruction=None, tools=None):
            self.system_instruction = system_instruction
            self.tools = tools

    types_mod.GenerateContentConfig = _Cfg
    types_mod.Tool = lambda **kw: SimpleNamespace(**kw)
    types_mod.FunctionDeclaration = lambda **kw: SimpleNamespace(**kw)
    types_mod.Schema = lambda **kw: SimpleNamespace(**kw)
    types_mod.Type = SimpleNamespace(OBJECT="OBJECT", INTEGER="INTEGER", STRING="STRING")

    genai_mod = ModuleType("google.genai")
    genai_mod.types = types_mod
    google_mod = ModuleType("google")
    google_mod.genai = genai_mod

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_mod)
    return types_mod


class _FakeCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class _FakeResponse:
    def __init__(self, function_calls):
        self.function_calls = function_calls


class _FakeModels:
    def __init__(self, calls, responses):
        self.calls = calls
        self.responses = responses

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents,
                           "tools": bool(config.tools), "system": bool(config.system_instruction)})
        return self.responses.pop(0)


def _client(responses):
    calls = []
    return SimpleNamespace(models=_FakeModels(calls, responses)), calls


def test_decide_tool_extracts_function_call(stub_genai, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    client, calls = _client([
        _FakeResponse([_FakeCall("lookup_booking", {"booking_id": 12})]),
    ])
    out = L.decide_tool(client, "check my booking 12")
    assert out == {"tool": "lookup_booking", "args": {"booking_id": 12}}
    assert calls[0]["model"] == "gemini-3.8-flash"
    assert calls[0]["tools"] is True and calls[0]["contents"] == "check my booking 12"


def test_decide_tool_none_when_no_function_call(stub_genai, monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    client, _ = _client([_FakeResponse(None)])
    assert L.decide_tool(client, "just chatting") is None


def test_decide_tool_defaults_model(stub_genai, monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    client, calls = _client([
        _FakeResponse([_FakeCall("cancel_booking", {"booking_id": 7})]),
    ])
    L.decide_tool(client, "cancel booking 7")
    assert calls[0]["model"] == "gemini-3.8-flash"


def test_make_llm_none_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert L.make_llm() is None


def test_make_llm_none_when_sdk_missing(monkeypatch):
    # sys.modules entry set to None makes `from google import genai` raise ImportError
    monkeypatch.setenv("GEMINI_API_KEY", "some-key")
    monkeypatch.setitem(sys.modules, "google", None)
    assert L.make_llm() is None
