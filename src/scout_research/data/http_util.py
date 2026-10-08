"""L1 — kleine HTTP-Helfer, die EDGAR- und Marktdaten-Client gemeinsam nutzen."""

from __future__ import annotations

import email.utils
import math
from datetime import datetime, timezone


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """`Retry-After` ist laut HTTP entweder eine Zahl Sekunden oder ein HTTP-Datum."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        seconds = None
    if seconds is not None:
        return max(seconds, 0.0) if math.isfinite(seconds) else None

    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - (now or datetime.now(timezone.utc))).total_seconds(), 0.0)
