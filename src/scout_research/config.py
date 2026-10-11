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
    output_language: Literal["de", "en"] = "de"

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

    @field_validator("anthropic_model", "anthropic_effort", "output_language", mode="before")
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
