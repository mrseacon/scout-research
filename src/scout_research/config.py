from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration. Lädt aus .env, dann Umgebungsvariablen."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    edgar_contact_email: str
    edgar_app_name: str = "Scout Research"

    finnhub_api_key: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    """Effort-Stufe (`output_config.effort`); der Modellvergleich läuft über diese Stufen (Plan, L9)."""
    anthropic_max_tokens: int = 8000
    """`max_tokens` je Modellaufruf inkl. adaptivem Thinking (Plan 12.8; Sean, 2026-10-11). Wer die Grenze erreicht,
    wird verworfen (V5); es gibt keinen Retry."""
    output_language: Literal["de", "en"] = "de"
    runs_dir: Path = Path("runs")
    """Ordner der Trace-Dateien (Plan 6, 12.10). Der Desktop liegt in iCloud: für Live-Läufe ein lokaler Pfad außerhalb."""

    budget_run_usd: float = 1.0
    """Budgetdeckel je Lauf in US-Dollar (Plan 12.8, TA-12b); zusätzlich zur Sitzungsgrenze, es gilt die strengste."""
    budget_day_usd: float = 3.0
    """Budgetdeckel je UTC-Tag über alle Läufe (Kostenbuch unter `runs_dir`)."""

    edgar_requests_per_second: int = 10
    finnhub_requests_per_minute: int = 55

    @field_validator("edgar_contact_email")
    @classmethod
    def _require_real_email(cls, value: str) -> str:
        if "@" not in value or value.strip() == "you@example.com":
            raise ValueError(
                "EDGAR_CONTACT_EMAIL muss eine echte Kontakt-Adresse sein "
                "(SEC Fair Access Policy verlangt einen aussagekräftigen User-Agent)."
            )
        return value

    @field_validator("anthropic_max_tokens", mode="before")
    @classmethod
    def _blank_max_tokens_is_default(cls, value: object) -> object:
        return 8000 if isinstance(value, str) and not value.strip() else value

    @field_validator("anthropic_max_tokens")
    @classmethod
    def _positive_max_tokens(cls, value: int) -> int:
        if value < 256:
            raise ValueError("ANTHROPIC_MAX_TOKENS muss mindestens 256 sein.")
        return value

    @field_validator("budget_run_usd", "budget_day_usd", mode="before")
    @classmethod
    def _blank_budget_is_default(cls, value: object, info) -> object:
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @field_validator("budget_run_usd", "budget_day_usd")
    @classmethod
    def _positive_budget(cls, value: float) -> float:
        if not value > 0 or value == float("inf"):
            raise ValueError("BUDGET_RUN_USD und BUDGET_DAY_USD müssen größer als 0 sein.")
        return value

    @field_validator("anthropic_model", "anthropic_effort", "output_language", "runs_dir", mode="before")
    @classmethod
    def _blank_means_default(cls, value: object, info) -> object:
        # `FOO=` in der .env liefert "" — das soll den Default bedeuten, nicht einen leeren Wert.
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @property
    def edgar_user_agent(self) -> str:
        return f"{self.edgar_app_name} ({self.edgar_contact_email})"


def get_settings() -> Settings:
    return Settings()
