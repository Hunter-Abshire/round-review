"""Identifying the agent from the ability icons, so a portrait misread cannot decide it."""

from pathlib import Path

import pytest

from round_review.errors import HudError
from round_review.vision.agent_icons import (
    AgentTemplates,
    icon_signature,
    identify_agent,
)
from round_review.vision.raster import Glyph, Gray


def gray(width: int, height: int, values: list[int]) -> Gray:
    return Gray(width, height, bytes(values))


BRIGHT = gray(4, 4, [255] * 16)
DARK = gray(4, 4, [0] * 16)
HALF = gray(4, 4, [255, 255, 0, 0] * 4)


class TestSignature:
    def test_a_crop_becomes_a_fixed_grid(self) -> None:
        sig = icon_signature(BRIGHT, threshold=128)
        assert len(sig.grid) == 12 and len(sig.grid[0]) == 12
        assert set("".join(sig.grid)) == {"#"}

    def test_a_dark_crop_is_all_off(self) -> None:
        assert set("".join(icon_signature(DARK, threshold=128).grid)) == {"."}


class TestTemplates:
    def test_learning_and_matching_a_kit(self) -> None:
        kit = (icon_signature(BRIGHT, 128), icon_signature(DARK, 128))
        templates = AgentTemplates({}).learn("Veto", kit)
        name, score = identify_agent(kit, templates, min_confidence=0.9)
        assert name == "Veto"
        assert score == pytest.approx(1.0)

    def test_a_different_kit_does_not_match(self) -> None:
        veto = (icon_signature(BRIGHT, 128), icon_signature(DARK, 128))
        omen = (icon_signature(DARK, 128), icon_signature(BRIGHT, 128))
        templates = AgentTemplates({}).learn("Veto", veto)
        assert identify_agent(omen, templates, min_confidence=0.9)[0] is None

    def test_the_closest_agent_wins(self) -> None:
        veto = (icon_signature(BRIGHT, 128),)
        omen = (icon_signature(DARK, 128),)
        templates = AgentTemplates({}).learn("Veto", veto).learn("Omen", omen)
        assert identify_agent((icon_signature(HALF, 128),), templates, 0.4)[0] in {"Veto", "Omen"}
        assert identify_agent(veto, templates, 0.9)[0] == "Veto"

    def test_a_kit_of_a_different_size_never_matches(self) -> None:
        templates = AgentTemplates({}).learn("Veto", (icon_signature(BRIGHT, 128),))
        two = (icon_signature(BRIGHT, 128), icon_signature(BRIGHT, 128))
        assert identify_agent(two, templates, 0.5)[0] is None

    def test_no_templates_means_no_answer(self) -> None:
        assert identify_agent((icon_signature(BRIGHT, 128),), AgentTemplates({}), 0.5) == (
            None,
            0.0,
        )

    def test_an_empty_kit_means_no_answer(self) -> None:
        templates = AgentTemplates({}).learn("Veto", (icon_signature(BRIGHT, 128),))
        assert identify_agent((), templates, 0.5) == (None, 0.0)

    def test_several_samples_per_agent_are_kept(self) -> None:
        one = (icon_signature(BRIGHT, 128),)
        two = (icon_signature(HALF, 128),)
        templates = AgentTemplates({}).learn("Veto", one).learn("Veto", two)
        assert len(templates.agents_to_kits["Veto"]) == 2
        assert identify_agent(two, templates, 0.9)[0] == "Veto"


class TestPersistence:
    def test_round_trips_through_a_file(self, tmp_path: Path) -> None:
        kit = (icon_signature(BRIGHT, 128), icon_signature(HALF, 128))
        path = AgentTemplates({}).learn("Veto", kit).save(tmp_path / "agents.json")
        loaded = AgentTemplates.load(path)
        assert identify_agent(kit, loaded, 0.9)[0] == "Veto"

    def test_a_missing_file_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert AgentTemplates.load(tmp_path / "nope.json").agents_to_kits == {}

    def test_a_corrupt_file_is_an_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "agents.json"
        bad.write_text("{", encoding="utf-8")
        with pytest.raises(HudError):
            AgentTemplates.load(bad)

    def test_agents_learned_are_listed(self) -> None:
        templates = AgentTemplates({}).learn("Veto", (icon_signature(BRIGHT, 128),))
        assert templates.agents() == ["Veto"]
        assert AgentTemplates({}).agents() == []


def test_glyph_is_reused_for_signatures() -> None:
    assert isinstance(icon_signature(BRIGHT, 128), Glyph)
