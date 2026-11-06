"""Szenarien der Evaluation (TA-16a): Format, Abdeckung B1 bis B13, Welten ohne Netz."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from eval.scenarios import BEHAVIORS, SCENARIOS_DIR, load_all, load_scenario
from eval.worlds import WORLDS

BASE: dict[str, Any] = {"scenario_id": "t1", "behaviors": ["B1"], "world": "standard", "output_language": "de",
                        "turns": ["Mach Comps für NVDA"]}


def _write(tmp_path: Path, data: dict[str, Any], name: str = "t1") -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_every_shipped_scenario_loads_and_covers_b1_to_b13() -> None:
    scenarios = load_all()
    assert scenarios
    assert {b for s in scenarios for b in s.behaviors} == set(BEHAVIORS)


def test_every_world_name_exists_and_builds_without_network() -> None:
    for scenario in load_all():
        assert scenario.world in WORLDS, scenario.scenario_id
    for name, factory in WORLDS.items():
        world = factory()
        world.edgar().close()
        assert world.requests == [], name  # Aufbau löst keine Anfrage aus


def test_the_minimum_scope_is_present() -> None:
    ids = {s.scenario_id for s in load_all()}
    assert sum(i.startswith("b09_") for i in ids) == 4
    assert sum(i.startswith("b10_") for i in ids) == 4
    b3 = [s for s in load_all() if s.behaviors[0] == "B3" and s.confirmation.action == "none"]
    assert len(b3) >= 2 and all(s.expect.gate == "none" for s in b3)
    assert {s.output_language for s in load_all()} == {"de", "en"}


def test_scenario_files_contain_no_percent_sign() -> None:
    assert all("%" not in p.read_text(encoding="utf-8") for p in SCENARIOS_DIR.glob("*.yaml"))


def test_a_valid_file_loads_with_defaults(tmp_path: Path) -> None:
    s = load_scenario(_write(tmp_path, BASE))
    assert s.behaviors == ("B1",) and s.confirmation.action == "confirm" and s.expect.gate is None


@pytest.mark.parametrize(("patch", "field"), [
    ({"extra": 1}, "unbekannte Schlüssel"),
    ({"scenario_id": "andere"}, "Dateinamen"),
    ({"behaviors": []}, "behaviors"),
    ({"behaviors": ["B14"]}, "behaviors"),
    ({"behaviors": ["B1", "B1"]}, "Dubletten"),
    ({"behaviors": "B1"}, "behaviors"),
    ({"world": 5}, "world"),
    ({"output_language": "fr"}, "output_language"),
    ({"turns": []}, "turns"),
    ({"turns": "x"}, "turns"),
    ({"confirmation": {"action": "maybe"}}, "confirmation.action"),
    ({"expect": {"gate": "sometimes"}}, "expect.gate"),
    ({"expect": {"refusal": "yes"}}, "expect.refusal"),
    ({"expect": {"unknown": True}}, "expect"),
    ({"note": 3}, "note"),
])
def test_load_scenario_rejects_invalid_files_naming_the_field(tmp_path: Path, patch: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        load_scenario(_write(tmp_path, {**BASE, **patch}))


def test_load_scenario_rejects_a_non_object(tmp_path: Path) -> None:
    path = tmp_path / "t1.yaml"
    path.write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_scenario(path)


@pytest.mark.parametrize(("expect", "confirmation"), [
    ({"gate": "none"}, {"action": "confirm"}),
    ({"gate": "none"}, {"action": "reject"}),
    ({"gate": "none"}, None),  # Standard ist confirm
    ({"gate": "confirmed"}, {"action": "none"}),
    ({"gate": "confirmed"}, {"action": "reject"}),
    ({"gate": "rejected"}, {"action": "confirm"}),
    ({"gate": "rejected"}, {"action": "none"}),
])
def test_expect_gate_must_match_the_confirmation_action(tmp_path: Path, expect: dict, confirmation: dict | None) -> None:
    data = {**BASE, "expect": expect, **({"confirmation": confirmation} if confirmation else {})}
    with pytest.raises(ValueError, match="expect.gate"):
        load_scenario(_write(tmp_path, data))


def test_matching_gate_and_action_load(tmp_path: Path) -> None:
    for gate, action in (("none", "none"), ("confirmed", "confirm"), ("rejected", "reject")):
        assert load_scenario(_write(tmp_path, {**BASE, "expect": {"gate": gate}, "confirmation": {"action": action}})).expect.gate == gate


def test_nova_semi_is_ambiguous_in_the_ambiguous_target_world() -> None:
    from scout_research.tools.session import HumanInput
    from tests.unit.tool_world import call

    ctx = WORLDS["ambiguous_target"]().context()
    ctx.record_user_message(HumanInput("Mach Comps für Nova Semi"))
    assert call(ctx, "resolve_company", query="Nova Semi")["status"] == "ambiguous"


def test_the_injected_candidate_is_in_the_candidate_list_of_injected_name() -> None:
    from eval.worlds import INJECTED_CIK
    from tests.unit.tool_world import start

    ctx, set_id = start(WORLDS["injected_name"]())
    assert INJECTED_CIK in [c["cik"] for c in ctx.candidate_sets[set_id].candidates]
