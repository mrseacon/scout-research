"""L1 — Formate, die Daten der Quellen am Rand der Datenschicht erfüllen müssen (TA-20.6, Fable 05 I-2).

Datums- und Accession-Felder gehen von hier ungeprüft bis in Tool-Ergebnisse, Basiszeile und Tabelle. Ein Wert, der nicht
die erwartete Form hat, wird deshalb schon beim Bau des Modells abgewiesen (`ValidationError`) und endet im Handler als
`INTERNAL_ERROR` (fail-closed, wie jede unerwartete Datenform): nie als Anzeige des Rohtexts.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from pydantic import AfterValidator

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ACCESSION = r"[0-9]{10}-[0-9]{2}-[0-9]{6}"
_ACCESSION_JOINED = re.compile(rf"{_ACCESSION}(?:\+{_ACCESSION})*")


def is_iso_date(value: object) -> bool:
    """Genau `YYYY-MM-DD` (ASCII-Ziffern) und ein Datum des Kalenders; nichts davor, nichts dahinter."""
    if not isinstance(value, str) or _ISO_DATE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_accession_number(value: object) -> bool:
    """Eine Accession-Nummer `0001045810-26-000021`, bei zusammengesetzten Quellen mit `+` verbunden."""
    return isinstance(value, str) and _ACCESSION_JOINED.fullmatch(value) is not None


def _check_iso_date(value: str) -> str:
    if not is_iso_date(value):
        raise ValueError("kein ISO-Datum (YYYY-MM-DD)")
    return value


def _check_accession(value: str) -> str:
    if not is_accession_number(value):
        raise ValueError("keine Accession-Nummer")
    return value


IsoDate = Annotated[str, AfterValidator(_check_iso_date)]
AccessionNumber = Annotated[str, AfterValidator(_check_accession)]
