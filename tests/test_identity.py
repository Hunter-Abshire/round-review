"""What each clip is: agent, map, side and when it was played."""

from datetime import UTC, datetime
from pathlib import Path

from round_review.identity import (
    ClipIdentity,
    identity_index,
    played_at_from,
    read_identities,
    write_identity,
)


class TestPlayedAt:
    def test_reads_the_date_outplayed_puts_in_the_filename(self) -> None:
        played = played_at_from(Path("Valorant_09-21-2026_22-4-48-733.mp4"), mtime=0.0)
        assert played is not None
        assert (played.year, played.month, played.day) == (2026, 9, 21)
        assert (played.hour, played.minute) == (22, 4)

    def test_handles_a_zero_padded_name(self) -> None:
        played = played_at_from(Path("Valorant_12-01-2026_08-05-09-120.mp4"), mtime=0.0)
        assert played is not None
        assert (played.month, played.day, played.hour) == (12, 1, 8)

    def test_falls_back_to_the_file_time(self) -> None:
        when = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        played = played_at_from(Path("clip.mp4"), mtime=when.timestamp())
        assert played == when

    def test_an_impossible_date_in_the_name_falls_back(self) -> None:
        when = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
        played = played_at_from(
            Path("Valorant_99-99-2026_99-99-99-000.mp4"), mtime=when.timestamp()
        )
        assert played == when


class TestIdentityStore:
    def identity(self, key: str = "k1", **overrides: object) -> ClipIdentity:
        base = {
            "key": key,
            "agent": "Jett",
            "map": "Ascent",
            "side": "attack",
            "source": "review",
        }
        base.update(overrides)
        return ClipIdentity(**base)  # type: ignore[arg-type]

    def test_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "identities.json"
        write_identity(path, self.identity())
        assert read_identities(path)["k1"] == self.identity()

    def test_a_later_write_replaces_an_earlier_one(self, tmp_path: Path) -> None:
        path = tmp_path / "identities.json"
        write_identity(path, self.identity(agent="Jett"))
        write_identity(path, self.identity(agent="Sova"))
        assert read_identities(path)["k1"].agent == "Sova"
        assert len(read_identities(path)) == 1

    def test_a_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert read_identities(tmp_path / "nope.json") == {}

    def test_a_corrupt_file_is_empty_rather_than_fatal(self, tmp_path: Path) -> None:
        path = tmp_path / "identities.json"
        path.write_text("not json")
        assert read_identities(path) == {}

    def test_creates_its_folder(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "identities.json"
        write_identity(path, self.identity())
        assert path.exists()


class TestFromReview:
    def test_takes_the_agent_and_map_a_review_already_found(self) -> None:
        from round_review.coaching.context import PlayerContext
        from round_review.coaching.review import WindowResult
        from round_review.video.windows import Window

        results = [
            WindowResult(
                window=Window(i, 0.0, 12.0, "tiled"),
                samples=(),
                findings=(),
                strengths=(),
                model_calls=1,
                warnings=(),
                context=PlayerContext(agent=agent, map=game_map),
            )
            for i, (agent, game_map) in enumerate(
                [(None, None), ("Jett", "Ascent"), ("Jett", "Ascent"), ("Sova", "Ascent")]
            )
        ]
        identity = identity_index("k1", results)
        assert identity is not None
        # the most common answer wins, so one odd window does not rename the clip
        assert identity.agent == "Jett"
        assert identity.map == "Ascent"
        assert identity.source == "review"

    def test_a_review_that_identified_nothing_gives_no_identity(self) -> None:
        from round_review.coaching.review import WindowResult
        from round_review.video.windows import Window

        results = [
            WindowResult(
                window=Window(0, 0.0, 12.0, "tiled"),
                samples=(),
                findings=(),
                strengths=(),
                model_calls=1,
                warnings=(),
            )
        ]
        assert identity_index("k1", results) is None

    def test_no_windows_gives_no_identity(self) -> None:
        assert identity_index("k1", []) is None
