"""Synthetic domain model. No real personal data — ever."""
from __future__ import annotations

from sqlalchemy import Column, Integer, String

from .db import Base


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True)
    customer_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)          # customer contact (synthetic)
    date = Column(String, nullable=False)          # ISO date string, e.g. "2026-09-18"
    time = Column(String, nullable=False)          # "HH:MM"
    service_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="confirmed")
