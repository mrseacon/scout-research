"""L3 — Einfache Zahlenerkennung für Texte, die gar keine Zahlen enthalten dürfen (Peer-Begründungen).

Der typisierte Backstop für übrige Modelltexte (Einheiten, Skalen, Rundungstoleranz) gehört zu Schritt 6 und ersetzt
dieses Modul dort; hier genügt die strenge Regel „keine Ziffer“.
"""

from __future__ import annotations

import re

_NUMERIC_RUN = re.compile(r"[^\W_]*[0-9²³¹¼½¾٠-٩۰-۹०-९０-９][^\W_]*")


def find_numbers(text: str, allowed_terms: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Fundstellen mit Ziffern. `allowed_terms` (Firmennamen und Ticker mit Ziffern, z. B. „3M“) werden vorher
    entfernt — sie sind Namen, keine Zahlen (Review F18)."""
    scrubbed = text
    for term in sorted({t for t in allowed_terms if t and any(ch.isnumeric() for ch in t)}, key=len, reverse=True):
        scrubbed = re.sub(re.escape(term), " ", scrubbed, flags=re.IGNORECASE)
    return [m.group(0) for m in _NUMERIC_RUN.finditer(scrubbed)]
