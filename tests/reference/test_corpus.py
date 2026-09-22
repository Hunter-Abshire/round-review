"""Turning the bundled knowledge and the player's own notes into retrievable passages."""

from pathlib import Path

import pytest

from round_review.coaching.knowledge import load_knowledge
from round_review.errors import KnowledgeError
from round_review.reference.corpus import (
    PASSAGE_KINDS,
    Passage,
    build_corpus,
    load_notes,
)


class TestBuildCorpus:
    def corpus(self) -> list[Passage]:
        return build_corpus(load_knowledge())

    def test_covers_every_agent_map_and_check(self) -> None:
        corpus = self.corpus()
        kinds = {p.kind for p in corpus}
        assert kinds <= set(PASSAGE_KINDS)
        assert {"agent", "map", "check", "drill"} <= kinds
        agents = {p.tags[0] for p in corpus if p.kind == "agent"}
        maps = {p.tags[0] for p in corpus if p.kind == "map"}
        assert len(agents) >= 28
        assert len(maps) >= 12
        assert len([p for p in corpus if p.kind == "check"]) >= 80

    def test_passage_ids_are_unique_and_stable(self) -> None:
        first = [p.id for p in self.corpus()]
        assert len(first) == len(set(first))
        assert first == [p.id for p in self.corpus()]

    def test_an_agent_passage_carries_its_abilities_and_mistakes(self) -> None:
        jett = next(p for p in self.corpus() if p.kind == "agent" and p.tags[0] == "jett")
        assert "Tailwind" in jett.text
        assert "jett" in jett.tags
        assert jett.title
        assert len(jett.text) > 200

    def test_a_map_passage_carries_its_callouts(self) -> None:
        ascent = next(p for p in self.corpus() if p.kind == "map" and p.tags[0] == "ascent")
        assert "Catwalk" in ascent.text or "Market" in ascent.text
        assert "ascent" in ascent.tags

    def test_a_check_passage_carries_the_fix_not_just_the_question(self) -> None:
        check = next(p for p in self.corpus() if p.kind == "check")
        assert check.tags  # the category, for filtering
        assert len(check.text) > 80

    def test_every_passage_has_text_worth_retrieving(self) -> None:
        assert all(p.text.strip() and p.title.strip() for p in self.corpus())

    def test_notes_are_folded_in_when_given(self, tmp_path: Path) -> None:
        (tmp_path / "lineups.md").write_text(
            "# Viper Bind\nWall from spawn covers B site plant.\n\n# Jett Ascent\nDash A main.\n"
        )
        corpus = build_corpus(load_knowledge(), notes=load_notes(tmp_path))
        notes = [p for p in corpus if p.kind == "note"]
        assert len(notes) == 2
        assert any("Wall from spawn" in p.text for p in notes)


class TestLoadNotes:
    def test_splits_a_markdown_file_on_headings(self, tmp_path: Path) -> None:
        (tmp_path / "n.md").write_text(
            "# Lineups\nfirst body\n\n## Bind B\nsecond body\n\n## Ascent A\nthird body\n"
        )
        notes = load_notes(tmp_path)
        assert [n.title for n in notes] == ["Lineups", "Bind B", "Ascent A"]
        assert "second body" in notes[1].text

    def test_a_file_with_no_headings_becomes_one_passage_titled_by_filename(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "team-calls.txt").write_text("we always default B on Lotus")
        (note,) = load_notes(tmp_path)
        assert note.title == "team-calls"
        assert "default B" in note.text

    def test_reads_nested_folders_and_both_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "one.md").write_text("# One\nbody one")
        (tmp_path / "two.txt").write_text("body two")
        (tmp_path / "ignored.pdf").write_bytes(b"%PDF")
        assert len(load_notes(tmp_path)) == 2

    def test_ignores_empty_sections_and_whitespace(self, tmp_path: Path) -> None:
        (tmp_path / "n.md").write_text("# Empty\n\n\n# Real\nsomething useful here\n")
        (note,) = load_notes(tmp_path)
        assert note.title == "Real"

    def test_a_missing_folder_is_not_an_error(self, tmp_path: Path) -> None:
        assert load_notes(tmp_path / "nope") == []
        assert load_notes(None) == []

    def test_note_passages_are_tagged_by_any_agent_or_map_they_mention(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "n.md").write_text("# Viper on Bind\nWall from spawn covers the B plant.")
        (note,) = load_notes(tmp_path)
        assert "viper" in note.tags
        assert "bind" in note.tags

    def test_an_oversized_file_is_refused_with_a_clear_error(self, tmp_path: Path) -> None:
        (tmp_path / "huge.md").write_text("x" * (2 * 1024 * 1024))
        with pytest.raises(KnowledgeError, match="too large"):
            load_notes(tmp_path)

    def test_note_ids_are_stable_and_unique_across_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("# Same\nbody a")
        (tmp_path / "b.md").write_text("# Same\nbody b")
        notes = load_notes(tmp_path)
        assert len({n.id for n in notes}) == 2
