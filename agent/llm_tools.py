"""Conversation + tool-calling LLM loop on Groq (OpenAI-compatible API).

The LLM is NOT the differentiator (per roadmap); TurnManager owns dispatch/
versioning/staleness. The LLM drives the conversation: every user utterance yields
either a tool call or a spoken reply, and fresh tool outcomes are confirmed in
natural language — so the agent behaves like a real receptionist.

The system prompt is built PER TURN with the current date/time so relative dates
("this Friday", "tomorrow") resolve correctly. When info is missing (name, phone,
date, time), the agent ASKS for it instead of guessing.

Uses the `openai` SDK pointed at Groq's base URL (identical wire protocol). Imports
are deferred so hermetic tests run network/key-free. Model id: GROQ_MODEL.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

_SERVICES = ("Haircut", "Blowout", "Hair Coloring", "Highlights", "Keratin Treatment",
             "Beard Trim", "Shampoo & Style", "Bridal Styling")
_PRICES = {"Haircut": "Rs 500", "Blowout": "Rs 700", "Hair Coloring": "Rs 2500",
           "Highlights": "Rs 3500", "Keratin Treatment": "Rs 4000",
           "Beard Trim": "Rs 300", "Shampoo & Style": "Rs 600",
           "Bridal Styling": "Rs 8000"}

_PERSONA = (
    "You are Riya, a voice receptionist at Glow & Go, a hair salon. You ONLY handle "
    "salon-appointment business: booking, finding, rescheduling, or cancelling "
    "appointments, and answering salon-related questions (services, prices, opening "
    "hours: 9 AM to 8 PM daily). If the customer asks about anything else — general "
    "knowledge, coding, news, other companies — politely decline in ONE short sentence "
    "and steer back to their appointment. You do not answer off-topic questions even "
    "partially. "
    "Speak in short, natural sentences like a real person on the phone. "
    "COLLECT missing information like a receptionist: ask for name, phone, service, "
    "date, or time one question at a time before booking. Resolve relative dates "
    "('tomorrow', 'this Saturday') using the current date given below. Services and "
    "prices: "
    + ", ".join(f"{s} ({p})" for s, p in _PRICES.items()) + ". "
    "Never invent booking data; if a tool is needed, call it. After any successful "
    "create/update/cancel, always confirm clearly what was booked or changed. "
    "Keep every reply under two sentences."
)

_DEFAULT_MODEL = "openai/gpt-oss-20b"
_BASE_URL = "https://api.groq.com/openai/v1"
MAX_HISTORY = 16

# OpenAI tool-calling schema (Groq-compatible)
_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_booking",
            "description": "Look up one booking by its numeric id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "integer", "description": "The booking id"},
                },
                "required": ["booking_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_bookings",
            "description": "Find a customer's bookings by their name or phone number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Customer name, e.g. 'Priya Sharma'"},
                    "phone": {"type": "string", "description": "Phone number or last digits"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_booking",
            "description": "Create a new booking. Only call when you have the customer's name, "
                           "date, and time (ask for anything missing first).",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string", "description": "Full customer name"},
                    "phone": {"type": "string", "description": "Contact phone"},
                    "date": {"type": "string", "description": "ISO date, e.g. 2026-09-18"},
                    "time": {"type": "string", "description": "HH:MM 24h, e.g. 14:30"},
                    "service_type": {"type": "string", "description": "Salon service: Haircut, Blowout, Hair Coloring, Highlights, Keratin Treatment, Beard Trim, Shampoo & Style, Bridal Styling"},
                },
                "required": ["customer_name", "date", "time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_booking",
            "description": "Change fields on an existing booking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "integer", "description": "The booking id"},
                    "changes": {
                        "type": "object",
                        "description": "Fields to change",
                        "properties": {
                            "date": {"type": "string", "description": "ISO date, e.g. 2026-09-18"},
                            "time": {"type": "string", "description": "HH:MM 24h, e.g. 14:30"},
                            "service_type": {"type": "string", "description": "Salon service: Haircut, Blowout, Hair Coloring, Highlights, Keratin Treatment, Beard Trim, Shampoo & Style, Bridal Styling"},
                            "status": {"type": "string", "description": "confirmed | pending | cancelled"},
                        },
                    },
                },
                "required": ["booking_id", "changes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_booking",
            "description": "Cancel a booking by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_id": {"type": "integer", "description": "The booking id"},
                },
                "required": ["booking_id"],
            },
        },
    },
]


def make_llm():
    """Return an OpenAI client pointed at Groq, or None if not configured."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None
    return OpenAI(base_url=_BASE_URL, api_key=api_key)


def _system_prompt() -> str:
    """Persona + CURRENT date/time context (computed per turn, never import time)."""
    now = datetime.now()
    return (f"{_PERSONA}\n\nCurrent date and time: {now.strftime('%A, %d %B %Y, %I:%M %p')}"
            f" (today is {now.strftime('%Y-%m-%d')}).")


def _trim(history: Optional[list]) -> list:
    return list((history or [])[-MAX_HISTORY:])


def _model() -> str:
    return os.getenv("GROQ_MODEL", _DEFAULT_MODEL)


def _chat(client, messages):
    return client.chat.completions.create(
        model=_model(),
        messages=messages,
        tools=_TOOLS,
        tool_choice="auto",
    )


def decide_tool(client, transcript: str) -> dict | None:
    """Tool-calling step: transcript -> {tool, args} or None (no tool). Kept for tests."""
    if client is None:
        return None
    resp = _chat(client, [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": transcript},
    ])
    msg = resp.choices[0].message
    if not msg.tool_calls:
        return None
    call = msg.tool_calls[0]
    import json as _json
    return {"tool": call.function.name,
            "args": _json.loads(call.function.arguments or "{}")}


def conversation_turn(client, transcript: str, history: Optional[list] = None) -> dict:
    """One turn with persona + date/time context + history.

    Returns {"tool": name, "args": {...}} (a booking tool to dispatch) or
    {"text": "..."} (a spoken reply — chit-chat, info collection, clarifications).
    """
    if client is None:
        return {"text": "I'm offline right now — the booking tools are not configured."}
    msgs = [{"role": "system", "content": _system_prompt()}]
    for h in _trim(history):
        msgs.append({"role": "user", "content": h})   # history kept flat (voice turns)
    msgs.append({"role": "user", "content": transcript})
    resp = _chat(client, msgs)
    msg = resp.choices[0].message
    if msg.tool_calls:
        call = msg.tool_calls[0]
        import json as _json
        return {"tool": call.function.name,
                "args": _json.loads(call.function.arguments or "{}")}
    return {"text": (msg.content or "Sorry, could you say that again?").strip()}


def confirmation_turn(client, tool_name: str, result, history: Optional[list] = None) -> str:
    """Natural-language confirmation after a fresh tool result (what the agent speaks)."""
    if client is None:
        return f"Done. {tool_name} result: {result}"
    prompt = (f"The {tool_name} tool returned: {result}. "
              "Confirm this to the customer in one short spoken sentence.")
    resp = client.chat.completions.create(
        model=_model(),
        messages=[
            {"role": "system", "content": _system_prompt()},
            *[{"role": "user", "content": h} for h in _trim(history)],
            {"role": "user", "content": prompt},
        ],
    )
    return (resp.choices[0].message.content or f"Done. {tool_name} result: {result}").strip()
