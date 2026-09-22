"""Which agent the player is on, decided by the ability icons rather than the portrait.

A vision model reading a 1280px frame will confidently name the wrong agent, and once it
does, every ability the coach suggests belongs to somebody else. The ability icons are
fixed art in fixed slots, so matching them is a lookup with a right answer.

Icons are learned from the player's own footage for the same reason the digits are: the
art changes between game versions and nothing about a bundled reference would survive it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from round_review.errors import HudError, RoundReviewError
from round_review.video.probe import CommandRunner, Recording
from round_review.vision.digits import similarity
from round_review.vision.hud import Region
from round_review.vision.raster import Glyph, Gray, normalize_glyph

# Icons are square, so they get a square grid rather than the tall one digits use.
ICON_GRID = 12

Kit = tuple[Glyph, ...]


def icon_signature(gray: Gray, threshold: int) -> Glyph:
    """One ability icon reduced to a comparable grid."""
    return normalize_glyph(
        gray, 0, 0, gray.width, gray.height, threshold, cols=ICON_GRID, rows=ICON_GRID
    )


def _kit_similarity(a: Kit, b: Kit) -> float:
    """Mean agreement across slots. Different slot counts are different kits, not a poor
    match: comparing three icons against four would silently compare the wrong ones."""
    if not a or len(a) != len(b):
        return 0.0
    return sum(similarity(x, y) for x, y in zip(a, b, strict=True)) / len(a)


@dataclass(frozen=True, slots=True)
class AgentTemplates:
    """Learned ability-icon sets per agent. Several sets per agent, because icons dim as
    charges are spent and a dimmed icon is still that agent's icon."""

    agents_to_kits: Mapping[str, tuple[Kit, ...]]

    def agents(self) -> list[str]:
        return sorted(self.agents_to_kits)

    def learn(self, agent: str, kit: Kit) -> AgentTemplates:
        grown = {k: list(v) for k, v in self.agents_to_kits.items()}
        grown.setdefault(agent, []).append(kit)
        return AgentTemplates({k: tuple(v) for k, v in grown.items()})

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "agents": {
                agent: [[{"grid": list(g.grid), "aspect": g.aspect} for g in kit] for kit in kits]
                for agent, kits in sorted(self.agents_to_kits.items())
            }
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> AgentTemplates:
        if not path.exists():
            return cls({})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))["agents"]
            return cls(
                {
                    str(agent): tuple(
                        tuple(
                            Glyph(tuple(str(r) for r in g["grid"]), float(g["aspect"])) for g in kit
                        )
                        for kit in kits
                    )
                    for agent, kits in raw.items()
                }
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise HudError(f"{path}: cannot read agent icon templates: {exc}") from exc


def identify_agent(
    kit: Sequence[Glyph], templates: AgentTemplates, min_confidence: float
) -> tuple[str | None, float]:
    """The agent whose learned icons best match these, or None when nothing is close."""
    best_name: str | None = None
    best_score = 0.0
    for agent, kits in templates.agents_to_kits.items():
        for learned in kits:
            score = _kit_similarity(tuple(kit), learned)
            if score > best_score:
                best_name, best_score = agent, score
    if best_score < min_confidence:
        return None, 0.0
    return best_name, best_score


def read_kit(
    recording: Recording,
    timestamp_s: float,
    ability_regions: Sequence[Region],
    runner: CommandRunner,
    out_dir: Path,
    threshold: int = -1,
) -> Kit:
    """Crop every ability slot at one moment. An empty kit when any crop fails, because a
    partial kit would be compared against the wrong slots."""
    from round_review.vision.state import crop_gray

    stem = f"kit_{timestamp_s:.1f}".replace(".", "_")
    icons: list[Glyph] = []
    for i, region in enumerate(ability_regions):
        gray = crop_gray(recording, timestamp_s, region, runner, out_dir / stem / f"icon{i}.pgm")
        if gray is None:
            return ()
        icons.append(icon_signature(gray, threshold))
    return tuple(icons)


def identify_from_frames(
    recording: Recording,
    timestamps: Sequence[float],
    ability_regions: Sequence[Region],
    runner: CommandRunner,
    templates: AgentTemplates,
    out_dir: Path,
    min_confidence: float,
    threshold: int = -1,
) -> tuple[str | None, float]:
    """Identify the agent from several moments and take the most common answer.

    One frame can catch an ability mid-animation or behind a killfeed popup; agreement
    across frames is what makes this worth trusting over the model's read.
    """
    if not ability_regions or not templates.agents_to_kits:
        return None, 0.0
    votes: dict[str, list[float]] = {}
    for timestamp in timestamps:
        kit = read_kit(recording, timestamp, ability_regions, runner, out_dir, threshold)
        name, score = identify_agent(kit, templates, min_confidence)
        if name is not None:
            votes.setdefault(name, []).append(score)
    if not votes:
        return None, 0.0
    winner = max(votes, key=lambda name: (len(votes[name]), sum(votes[name])))
    return winner, sum(votes[winner]) / len(votes[winner])


def crop_ability_icons(
    recording: Recording,
    timestamp_s: float,
    ability_regions: Sequence[Region],
    runner: CommandRunner,
    out_dir: Path,
) -> list[Path]:
    """Cut each ability slot to its own image file, for showing to a model.

    JPEG rather than the PGM the template matcher uses: this is going into a prompt, and a
    grayscale bitmap of a stylised icon is much harder to recognise than the colour art.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, region in enumerate(ability_regions):
        x, y, width, height = region.in_pixels(recording.width, recording.height)
        out = out_dir / f"ability{i}.jpg"
        try:
            runner.run(
                [
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{timestamp_s:.3f}",
                    "-i",
                    str(recording.path),
                    "-frames:v",
                    "1",
                    "-vf",
                    (f"crop={width}:{height}:{x}:{y},scale={width * 4}:{height * 4}:flags=lanczos"),
                    "-q:v",
                    "2",
                    str(out),
                ]
            )
        except (RoundReviewError, OSError):
            return []
        paths.append(out)
    return paths
