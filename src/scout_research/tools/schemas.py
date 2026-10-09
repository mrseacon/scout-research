"""L3 — Eingabemodelle der Modell-Tools und das daraus erzeugte JSON-Schema (Phase-3-Plan, Abschnitt 2).

Die Pydantic-Modelle sind die Quelle und die **maßgebliche Prüfung auf dem Server** (auch für MCP). Das an die API
gesendete Schema ist eine Teilmenge: `strict: true` unterstützt keine Längen-, Wertebereichs- und `maxItems`-Grenzen,
`minItems` nur mit 0 oder 1 und nur einfache Muster (Review F11, geprüft gegen die Anthropic-Doku). `strict_schema`
entfernt diese Grenzen aus dem gesendeten Schema und schreibt sie in die `description` des Feldes.
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from scout_research.domain.periods import PeriodSelector

Cik = Annotated[str, StringConstraints(pattern=r"^\d{1,10}$")]
"""CIK als Ziffernfolge (die API liefert sie nullgepolstert; kürzere Eingaben werden gepolstert)."""

FinancialMetric = Literal[
    "revenue", "ebit", "ebitda", "net_income", "total_assets", "total_debt", "cash", "shares_outstanding"
]
ALL_METRICS: tuple[str, ...] = (
    "revenue", "ebit", "ebitda", "net_income", "total_assets", "total_debt", "cash", "shares_outstanding"
)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResolveCompanyInput(_Input):
    query: str = Field(min_length=1, max_length=100, description="Ticker oder Firmenname, so wie der Nutzer ihn nennt.")


class SizeRange(_Input):
    min: float = Field(0.2, ge=0.05, le=1, description="Untergrenze des Umsatzes relativ zum Ziel.")
    max: float = Field(5.0, ge=1, le=20, description="Obergrenze des Umsatzes relativ zum Ziel.")


class FindPeerCandidatesInput(_Input):
    target_cik: Cik = Field(description="CIK des Ziels aus resolve_company.")
    period: PeriodSelector | None = Field(None, description="Weglassen = jüngstes Geschäftsjahr (nur das ist möglich).")
    size_range: SizeRange | None = None


class PeerPick(_Input):
    cik: Cik
    rationale: str = Field(min_length=1, max_length=300, description="Ein Satz zum Geschäftsmodell, ohne Zahlen.")


class Exclusion(_Input):
    cik: Cik
    reason: str = Field(min_length=1, max_length=300, description="Grund ohne Zahlen.")


class Addition(_Input):
    query: str = Field(min_length=1, max_length=100, description="Name oder Ticker, genau so wie vom Nutzer genannt.")


class ProposePeerSetInput(_Input):
    candidate_set_id: str = Field(min_length=1, max_length=64)
    peers: list[PeerPick] = Field(min_length=1, max_length=15)
    notable_exclusions: list[Exclusion] | None = Field(None, max_length=15)
    user_requested_additions: list[Addition] | None = Field(None, max_length=15)


class ComputeCompsTableInput(_Input):
    peer_set_id: str = Field(min_length=1, max_length=64, description="Handle des vom Menschen bestätigten Peer-Sets (ps_…).")


class SubmitCommentaryInput(_Input):
    comps_table_id: str = Field(min_length=1, max_length=64, description="Handle der Comps-Tabelle (ct_…).")
    text: str = Field(min_length=1, max_length=4000, description="Kommentar mit Slots statt Zahlen.")


class GetFinancialsInput(_Input):
    cik: Cik
    period: PeriodSelector | None = None
    metrics: list[FinancialMetric] | None = Field(None, description="Weglassen = alle.")

    @field_validator("metrics")
    @classmethod
    def _unique(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("metrics enthält Dubletten")
        return value


class GetMarketDataInput(_Input):
    ticker: str = Field(min_length=1, max_length=10)


PERIOD_SELECTOR_DESCRIPTION = "Höchstens ein Feld setzen. Leer = jüngstes Geschäftsjahr."

_UNSUPPORTED = ("minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                "multipleOf", "maxItems", "pattern")


def _describe(key: str, value: Any) -> str:
    return {"minLength": f"mindestens {value} Zeichen", "maxLength": f"höchstens {value} Zeichen",
            "minimum": f"mindestens {value}", "maximum": f"höchstens {value}",
            "exclusiveMinimum": f"größer als {value}", "exclusiveMaximum": f"kleiner als {value}",
            "multipleOf": f"Vielfaches von {value}", "maxItems": f"höchstens {value} Einträge",
            "minItems": f"mindestens {value} Einträge", "pattern": f"Muster {value}"}[key]


def _strip(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strip(v) for k, v in node.items()}
    notes = [_describe(k, out.pop(k)) for k in _UNSUPPORTED if k in out]
    if out.get("minItems", 0) > 1:
        notes.append(_describe("minItems", out.pop("minItems")))
    if notes and out.get("type") != "object":
        text = "; ".join(notes)
        out["description"] = f"{out['description']} ({text})" if out.get("description") else f"({text})"
    return out


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON-Schema für `strict: true`: ohne die Grenzen, die die API ablehnt (sie stehen stattdessen in der
    `description`); Objekte mit `additionalProperties: false`. Geprüft wird weiter mit dem Pydantic-Modell."""
    schema = _strip(copy.deepcopy(model.model_json_schema()))
    period = schema.get("$defs", {}).get("PeriodSelector")
    if period is not None:  # statt der Entwickler-Docstring aus L2 eine kurze Beschreibung für das Modell
        period["description"] = PERIOD_SELECTOR_DESCRIPTION
        period["properties"]["period_end"]["description"] = "Exaktes Geschäftsjahresende (ISO-Datum). Bevorzugt."
        period["properties"]["fiscal_year"]["description"] = "Geschäftsjahr laut Filing (ungenau; period_end ist eindeutig)."
    return schema
