"""Budgets und Preistabelle der Agent-Schleife (Plan 12.8, L1, L7, V3, V5).

Alle Grenzen und Preise stehen **hier** — nicht verteilt im Code (12.8). Kosten entstehen nur aus der gemeldeten
`usage` jedes Aufrufs (Input, Output, Cache-Read, Cache-Write getrennt).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Price:
    """US-Dollar je Million Token."""

    input: float
    output: float
    cache_read: float
    cache_write: float


PRICES: dict[str, Price] = {
    # Sonnet 5.5: $2 / $10, Cache-Read $0,20 (belegt, Plan Abschnitt 9). Cache-Write mit 1,25 × Input angenommen:
    # ungeprüft; die Annahme ist konservativ (eher zu hoch), sie belastet nur das Budget.
    "claude-sonnet-5-5": Price(input=2.0, output=10.0, cache_read=0.20, cache_write=2.5),
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def context_tokens(self) -> int:
        """Alles, was der Aufruf an Kontext gelesen hat (Grundlage der Kontextgrenze, L7)."""
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens


def cost_usd(usage: Usage, price: Price) -> float:
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.cache_write
    ) / 1_000_000


@dataclass(frozen=True)
class LoopConfig:
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"
    language: str = "de"
    max_tokens: int = 8000
    max_calls_per_turn: int = 10
    max_tools_per_turn: int = 12
    turn_seconds: float = 600.0
    call_seconds: float = 300.0
    stall_seconds: float = 60.0
    cost_warning_usd: float = 1.0
    cost_limit_usd: float = 2.0
    context_limit_tokens: int = 150_000
    prices: dict[str, Price] = field(default_factory=lambda: dict(PRICES))

    @property
    def price(self) -> Price:
        try:
            return self.prices[self.model]
        except KeyError:
            raise ValueError(f"Kein Preis für das Modell {self.model!r} in der Preistabelle") from None

    @classmethod
    def from_settings(cls, settings) -> LoopConfig:
        return cls(model=settings.anthropic_model, effort=settings.anthropic_effort,
                   language=settings.output_language, max_tokens=settings.anthropic_max_tokens)
