from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Zentrale Konfiguration. Lädt aus .env, dann Umgebungsvariablen."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    edgar_contact_email: str
    edgar_app_name: str = "Scout Research"

    finnhub_api_key: str | None = None
    anthropic_api_key: str | None = None

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

    @property
    def edgar_user_agent(self) -> str:
        return f"{self.edgar_app_name} ({self.edgar_contact_email})"


def get_settings() -> Settings:
    return Settings()
