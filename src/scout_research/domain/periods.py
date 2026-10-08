"""L2 — Periodenauswahl (Foundation Doc 7.3.1, D9).

`PeriodSelector` wählt das Geschäftsjahr, für das Kennzahlen gebaut werden. Ohne Angabe gilt wie bisher
das jüngste 10-K. Enthält keine Extraktionslogik (die liegt in `metrics.py`), damit `metrics.py` und
`peers.py` dieses Modul ohne Zirkelbezug importieren können.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, model_validator

CALENDAR_YEAR_OFFSET_DAYS = 180
"""`calendar_year = Jahr(Periodenende − 180 Tage)`. Gegen den `frames`-Endpunkt gemessen (Foundation Doc
7.3.1): 31/31 Firmen mit Geschäftsjahresende in allen Monaten. Beobachtetes, undokumentiertes SEC-
Verhalten — deshalb prüft `peers.resolve_calendar_year` das Ergebnis gegen den Frame, statt ihm zu
vertrauen."""


class PeriodSelector(BaseModel):
    """Höchstens ein Feld gesetzt; kein Feld = jüngstes 10-K.

    - `period_end` ist kanonisch: exaktes Geschäftsjahresende (ISO-Datum).
    - `fiscal_year` ist Best Effort: das SEC-Feld `fy` des 10-K, das jeder Filer selbst setzt. Dasselbe
      `fiscal_year` kann bei verschiedenen Firmen verschiedene Zeiträume meinen; für Vergleiche zwischen
      Unternehmen ist `period_end` zu verwenden.
    """

    model_config = {"extra": "forbid"}

    period_end: date | None = None
    fiscal_year: int | None = None

    @model_validator(mode="after")
    def _at_most_one(self) -> "PeriodSelector":
        if self.period_end is not None and self.fiscal_year is not None:
            raise ValueError("PeriodSelector: höchstens ein Feld (period_end oder fiscal_year) setzen.")
        return self

    @property
    def is_latest(self) -> bool:
        return self.period_end is None and self.fiscal_year is None


class PeriodNotAvailable(Exception):
    """Die gewünschte Periode gibt es für das Unternehmen nicht. Nie ein stilles "nächstes Jahr"."""

    code = "PERIOD_NOT_AVAILABLE"

    def __init__(self, cik: str, requested: PeriodSelector, available_period_ends: list[str]) -> None:
        self.cik = cik
        self.requested = requested
        self.available_period_ends = available_period_ends
        wanted = (
            f"Periodenende {requested.period_end}"
            if requested.period_end is not None
            else f"Fiscal Year {requested.fiscal_year}"
        )
        available = ", ".join(available_period_ends) if available_period_ends else "keine"
        super().__init__(f"Für CIK {cik} gibt es kein 10-K für {wanted}. Verfügbare Periodenenden: {available}.")


class HistoricalValuationNotSupported(Exception):
    """Multiples und Peer-Statistik gibt es nur für die aktuelle Periode (Kurse sind immer "heute")."""

    code = "HISTORICAL_VALUATION_NOT_SUPPORTED"

    def __init__(self, tickers: list[str]) -> None:
        self.tickers = tickers
        super().__init__(
            "Multiples sind nur für die jeweils aktuelle Periode möglich (der Kurs ist der heutige). "
            f"Historische Periode bei: {', '.join(tickers)}. Historische Perioden liefern nur Financials, "
            "Margen und Wachstum."
        )


def derive_calendar_year(period_end: date | str) -> int:
    """Kalenderjahr des `frames`-Endpunkts, in dem ein Geschäftsjahr mit diesem Ende auftaucht."""
    if isinstance(period_end, str):
        period_end = date.fromisoformat(period_end)
    return (period_end - timedelta(days=CALENDAR_YEAR_OFFSET_DAYS)).year
