"""Tool-calling LLM loop on Google Gemini. Deliberately simple — per the roadmap it is
NOT the differentiator.

Uses the google-genai SDK (imports deferred so hermetic tests run without network or a
key when the client is stubbed). Model id comes from GEMINI_MODEL and is validated
against the live model catalog by `evidence/preflight.py`. The LLM only chooses intent +
arguments; the TurnManager owns dispatch/versioning/staleness — independently of the LLM.
"""
from __future__ import annotations

import os

SYSTEM_PROMPT = (
    "You are a concise voice booking assistant. Use the provided tools to look up, "
    "update, or cancel bookings. Confirm the outcome in one short spoken sentence."
)

_DEFAULT_MODEL = "gemini-3.8-flash"


def _tool_declarations():
    from google.genai import types

    def _schema(t, description=None, properties=None, required=None):
        return types.Schema(type=t, description=description,
                            properties=properties, required=required)

    return [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="lookup_booking",
                description="Look up a booking by id.",
                parameters=_schema(
                    types.Type.OBJECT,
                    properties={"booking_id": _schema(types.Type.INTEGER, "Booking id")},
                    required=["booking_id"],
                ),
            ),
            types.FunctionDeclaration(
                name="update_booking",
                description="Change fields on a booking (date, time, service_type, status).",
                parameters=_schema(
                    types.Type.OBJECT,
                    properties={
                        "booking_id": _schema(types.Type.INTEGER, "Booking id"),
                        "changes": _schema(
                            types.Type.OBJECT,
                            "Field -> new value",
                            properties={
                                "date": _schema(types.Type.STRING, "ISO date, e.g. 2026-09-18"),
                                "time": _schema(types.Type.STRING, "HH:MM"),
                                "service_type": _schema(types.Type.STRING),
                                "status": _schema(types.Type.STRING),
                            },
                        ),
                    },
                    required=["booking_id", "changes"],
                ),
            ),
            types.FunctionDeclaration(
                name="cancel_booking",
                description="Cancel a booking by id.",
                parameters=_schema(
                    types.Type.OBJECT,
                    properties={"booking_id": _schema(types.Type.INTEGER, "Booking id")},
                    required=["booking_id"],
                ),
            ),
        ])
    ]


def make_llm():
    """Return a google-genai Client, or None if not configured (offline demo)."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai
    except ImportError:
        return None
    return genai.Client(api_key=api_key)


def decide_tool(client, transcript: str) -> dict | None:
    """Single tool-calling step: transcript -> {tool, args} or None (no tool)."""
    if client is None:
        return None
    from google.genai import types

    resp = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", _DEFAULT_MODEL),
        contents=transcript,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=_tool_declarations(),
        ),
    )
    calls = getattr(resp, "function_calls", None)
    if not calls:
        return None
    call = calls[0]
    return {"tool": call.name, "args": dict(call.args or {})}
