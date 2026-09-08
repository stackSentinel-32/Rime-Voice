"""Booking tool logic as pure functions over a SQLAlchemy session.

Design note (write-safety): mutating tools are split into *validate* (read + compute
the proposed row, NO persistence) and *commit* (persist). The orchestrator only calls
`commit_booking` for a NON-STALE result, so a cancelled/stale tool call can never write
to Postgres. This is what makes the "never applied to state" half of the claim true for
writes, not just reads.
"""
from __future__ import annotations

from .models import Booking


class BookingNotFound(Exception):
    pass


def booking_to_dict(b: Booking) -> dict:
    return {
        "id": b.id,
        "customer_name": b.customer_name,
        "phone": b.phone,
        "date": b.date,
        "time": b.time,
        "service_type": b.service_type,
        "status": b.status,
    }


def _get(session, booking_id) -> Booking:
    b = session.get(Booking, int(booking_id))
    if b is None:
        raise BookingNotFound(f"booking {booking_id} not found")
    return b


# ---- reads ----------------------------------------------------------------
def lookup_booking(session, booking_id) -> dict:
    return booking_to_dict(_get(session, booking_id))


def find_bookings(session, customer_name: str = "", phone: str = "") -> list[dict]:
    """Receptionist flow: find a customer's bookings by name or phone (synthetic data)."""
    q = session.query(Booking)
    if phone:
        q = q.filter(Booking.phone.contains(phone.replace(" ", "")))
    elif customer_name:
        # match any name token ("Priya Sharma" -> "priya" or "sharma")
        token = customer_name.strip().split()[0].lower()
        q = q.filter(Booking.customer_name.ilike(f"%{token}%"))
    rows = q.order_by(Booking.date).all()
    return [booking_to_dict(b) for b in rows[:8]]


# ---- validate (no persistence) --------------------------------------------
_EDITABLE = {"date", "time", "service_type", "status", "customer_name", "phone"}


def validate_create(session, customer_name: str, date: str, time: str,
                    service_type: str, phone: str = "") -> dict:
    """Propose a NEW booking (no persistence until the fresh commit)."""
    return {
        "id": None,  # assigned at commit
        "customer_name": customer_name,
        "phone": phone or None,
        "date": date,
        "time": time,
        "service_type": service_type,
        "status": "confirmed",
    }


def validate_update(session, booking_id, changes: dict) -> dict:
    proposal = booking_to_dict(_get(session, booking_id))
    for k, v in (changes or {}).items():
        if k in _EDITABLE:
            proposal[k] = v
    proposal.setdefault("status", "confirmed")
    return proposal


def validate_cancel(session, booking_id) -> dict:
    proposal = booking_to_dict(_get(session, booking_id))
    proposal["status"] = "cancelled"
    return proposal


# ---- commit (persistence; only reached on a non-stale apply) --------------
def commit_booking(session, proposal: dict) -> dict:
    if proposal.get("id") is None:                     # new booking (create flow)
        b = Booking(
            customer_name=proposal["customer_name"],
            phone=proposal.get("phone"),
            date=proposal["date"],
            time=proposal["time"],
            service_type=proposal.get("service_type", "Haircut"),
            status=proposal.get("status", "confirmed"),
        )
        session.add(b)
        session.commit()
        return booking_to_dict(b)
    b = session.get(Booking, int(proposal["id"]))
    if b is None:
        raise BookingNotFound(f"booking {proposal['id']} not found")
    for k in ("customer_name", "phone", "date", "time", "service_type", "status"):
        if proposal.get(k) is not None:
            setattr(b, k, proposal[k])
    session.commit()
    return booking_to_dict(b)
