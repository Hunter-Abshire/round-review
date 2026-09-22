"""Every setting needs a label, a type and an explanation, so the app can offer them all."""

import dataclasses
from pathlib import Path

import pytest

from round_review.config import (
    EXCLUDED_FROM_UI,
    FIELDS,
    Config,
    field_spec,
    load_config,
    write_config,
)
from round_review.errors import ConfigError


def test_every_setting_is_either_offered_or_deliberately_excluded() -> None:
    described = {f.name for f in FIELDS}
    actual = {f.name for f in dataclasses.fields(Config)}
    assert described | EXCLUDED_FROM_UI == actual, (
        "a Config field is neither described nor excluded"
    )
    assert not described & EXCLUDED_FROM_UI


def test_each_field_explains_itself() -> None:
    for spec in FIELDS:
        assert spec.label and not spec.label.endswith("."), spec.name
        assert spec.help.endswith("."), spec.name
        assert spec.group, spec.name
        assert spec.kind in {"bool", "int", "float", "text", "path", "choice"}, spec.name


def test_a_choice_field_knows_where_its_choices_come_from() -> None:
    for spec in FIELDS:
        if spec.kind == "choice":
            assert bool(spec.choices) != bool(spec.choices_from), spec.name
        else:
            assert not spec.choices and not spec.choices_from, spec.name


def test_the_model_list_is_filled_at_runtime() -> None:
    assert field_spec("model").choices_from == "ollama_models"


def test_coverage_offers_the_two_modes() -> None:
    assert set(field_spec("coverage").choices) == {"full", "sampled"}


def test_numeric_fields_that_must_be_positive_say_so() -> None:
    assert field_spec("window_s").minimum == pytest.approx(0.1)
    assert field_spec("max_windows").minimum == 0  # 0 means unlimited


def test_groups_are_ordered_for_display() -> None:
    from round_review.config import GROUPS

    assert GROUPS[0] == "Recordings"
    assert set(GROUPS) == {f.group for f in FIELDS}


def test_an_unknown_field_name_is_an_error() -> None:
    with pytest.raises(KeyError):
        field_spec("nope")


class TestWriteConfig:
    def test_writes_values_that_load_back(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        write_config(path, {"model": "qwen3-vl:4b", "coverage": "sampled", "max_span_s": 60.0})
        cfg = load_config(path, env={}, data_dir=tmp_path)
        assert cfg.model == "qwen3-vl:4b"
        assert cfg.coverage == "sampled"
        assert cfg.max_span_s == 60.0

    def test_keeps_settings_it_was_not_asked_to_change(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        write_config(path, {"model": "mine", "fps": 0.5})
        write_config(path, {"model": "other"})
        cfg = load_config(path, env={}, data_dir=tmp_path)
        assert cfg.model == "other"
        assert cfg.fps == 0.5

    def test_writes_booleans_paths_and_numbers_correctly(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        write_config(
            path,
            {
                "situation_pass": False,
                "notes_dir": str(tmp_path / "notes"),
                "num_ctx": 32768,
                "hud_min_confidence": 0.75,
            },
        )
        cfg = load_config(path, env={}, data_dir=tmp_path)
        assert cfg.situation_pass is False
        assert cfg.notes_dir == tmp_path / "notes"
        assert cfg.num_ctx == 32768
        assert cfg.hud_min_confidence == pytest.approx(0.75)

    def test_clearing_a_value_removes_it(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        write_config(path, {"notes_dir": str(tmp_path / "n")})
        write_config(path, {"notes_dir": None})
        assert load_config(path, env={}, data_dir=tmp_path).notes_dir is None
        assert "notes_dir" not in path.read_text()

    def test_an_invalid_value_is_refused_and_the_file_is_untouched(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        write_config(path, {"model": "good"})
        before = path.read_text()
        with pytest.raises(ConfigError, match="coverage"):
            write_config(path, {"coverage": "sideways"})
        assert path.read_text() == before

    def test_an_unknown_setting_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="bogus"):
            write_config(tmp_path / "config.toml", {"bogus": 1})

    def test_a_field_excluded_from_the_ui_cannot_be_written(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="reports_dir"):
            write_config(tmp_path / "config.toml", {"reports_dir": "/tmp/x"})

    def test_the_written_file_is_commented_by_group(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        write_config(path, {"model": "m", "coverage": "full"})
        text = path.read_text()
        assert "# Model" in text or "# Review" in text
        assert text.startswith("#")

    def test_creates_the_folder_if_it_is_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deep" / "config.toml"
        write_config(path, {"model": "m"})
        assert path.exists()
