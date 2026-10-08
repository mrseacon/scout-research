"""L3 — Wörtlichkeitsregel (Phase-3-Plan, E2 A): Steht die Anfrage, mit der eine Firma aufgelöst wurde, wörtlich in
einer Nutzernachricht?

Die Regel gilt für die **Anfrage (`query`)**, nicht für den SEC-Namen:
- ganze Wörter bzw. Wortfolgen (Wortgrenzen);
- Namen: Groß-/Kleinschreibung egal;
- Ticker mit höchstens fünf Zeichen: nur wenn der Nutzer ihn **groß** oder mit **`$`** geschrieben hat
  (sonst träfe „es“ den Ticker ES und „an“ den Ticker AN);
- nur exakte Treffer (`ticker_exact`, `name_exact`).
Als Nutzernachricht zählt ausschließlich, was der Host beim Eingang protokolliert hat — nie ein Tool-Ergebnis.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

MAX_TICKER_LENGTH_FOR_CASE_RULE = 5
EXACT_MATCHES = ("ticker_exact", "name_exact")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _name_in(query: str, message: str) -> bool:
    words = _norm(query).split()
    if not words:
        return False
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)"
    return re.search(pattern, _norm(message), flags=re.IGNORECASE) is not None


def _ticker_in(core: str, message: str) -> bool:
    message = _norm(message)
    upper = re.escape(core.upper())
    # groß geschrieben: nicht Teil eines längeren Tokens ("BRK-B" enthält kein "B"), Satzzeichen-Punkt erlaubt
    if re.search(rf"(?<![\w\-.$]){upper}(?![\w\-])(?!\.\w)", message):
        return True
    return re.search(rf"\${upper}(?![\w\-])(?!\.\w)", message, flags=re.IGNORECASE) is not None


def query_is_literal(query: str, match: str, messages: Iterable[str]) -> bool:
    """True, wenn `query` (für einen Treffer der Art `match`) in mindestens einer der Nutzernachrichten steht."""
    if match not in EXACT_MATCHES:
        return False
    core = _norm(query).strip().lstrip("$").strip()
    if not core:
        return False
    use_ticker_rule = match == "ticker_exact" and len(core) <= MAX_TICKER_LENGTH_FOR_CASE_RULE
    check = _ticker_in if use_ticker_rule else _name_in
    return any(check(core, message) for message in messages)
