"""Deterministic synthetic booking seeder (30-50 rows). Synthetic data only."""
from __future__ import annotations

import os
import random

from .db import Base, make_engine, make_session_factory
from .models import Booking

_FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Rohan", "Ananya", "Vivaan", "Ishita",
          "Arjun", "Sara", "Reyansh", "Anika", "Advait", "Nisha", "Dhruv", "Priya",
          "Kiaan", "Tara", "Veer", "Zara"]
_LAST = ["Sharma", "Iyer", "Khan", "Nair", "Gupta", "Reddy", "Bose", "Mehta", "Rao", "Das"]
_SERVICES = ["Haircut", "Blowout", "Hair Coloring", "Highlights",
             "Keratin Treatment", "Beard Trim", "Shampoo & Style", "Bridal Styling"]
_DATES = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15",
          "2026-09-16", "2026-09-17", "2026-09-18"]
_TIMES = ["09:00", "09:30", "10:00", "10:30", "11:00", "14:00", "14:30", "15:00", "15:30", "16:00"]
_STATUSES = ["confirmed", "confirmed", "confirmed", "pending"]


def build_rows(n: int = 40, seed: int = 42) -> list[Booking]:
    rng = random.Random(seed)
    return [
        Booking(
            id=i,
            customer_name=f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
            phone=f"+91-9{rng.randint(10**8, 10**9 - 1)}",   # synthetic Indian mobile
            date=rng.choice(_DATES),
            time=rng.choice(_TIMES),
            service_type=rng.choice(_SERVICES),
            status=rng.choice(_STATUSES),
        )
        for i in range(1, n + 1)
    ]


def seed(session_factory, n: int = 40, reset: bool = True) -> int:
    with session_factory() as s:
        if reset:
            s.query(Booking).delete()
            s.commit()
        if s.query(Booking).count() == 0:
            s.add_all(build_rows(n))
            s.commit()
        return s.query(Booking).count()


def main() -> None:
    engine = make_engine()
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    count = seed(sf, n=int(os.getenv("SEED_ROWS", "40")))
    print(f"seeded {count} bookings into {engine.url}")


if __name__ == "__main__":
    main()
