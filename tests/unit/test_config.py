import pytest
from pydantic import ValidationError

from scout_research.config import Settings

_EMAIL = "tester@example.org"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, edgar_contact_email=_EMAIL, **overrides)


def test_agent_defaults():
    settings = _settings()
    assert settings.anthropic_model == "claude-sonnet-5-5"
    assert settings.anthropic_compare_model is None
    assert settings.anthropic_effort == "medium"
    assert settings.output_language == "de"


def test_agent_settings_from_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("ANTHROPIC_COMPARE_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("ANTHROPIC_EFFORT", "high")
    monkeypatch.setenv("OUTPUT_LANGUAGE", "en")
    settings = _settings()
    assert settings.anthropic_model == "claude-opus-5-5"
    assert settings.anthropic_compare_model == "claude-haiku-4-5-20251001"
    assert settings.anthropic_effort == "high"
    assert settings.output_language == "en"


def test_blank_values_fall_back_to_defaults(monkeypatch):
    # `FOO=` in der .env darf weder ein leeres Modell noch eine leere Sprache ergeben.
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_COMPARE_MODEL", "ANTHROPIC_EFFORT", "OUTPUT_LANGUAGE"):
        monkeypatch.setenv(name, "")
    settings = _settings()
    assert settings.anthropic_model == "claude-sonnet-5-5"
    assert settings.anthropic_compare_model is None
    assert settings.anthropic_effort == "medium"
    assert settings.output_language == "de"


@pytest.mark.parametrize("field,value", [("anthropic_effort", "extreme"), ("output_language", "fr")])
def test_invalid_choices_are_rejected(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})


@pytest.mark.live
def test_live_marker_is_deselected_by_default():
    pytest.fail("Tests mit Marker 'live' dürfen in der Standardsuite nicht laufen (addopts -m 'not live').")
