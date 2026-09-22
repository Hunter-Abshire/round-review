"""Turning a list of per-window findings into what a coach would actually hand you.

The shape and the numbers here come from coaching-feedback research and from what existing
review products ship; docs/coaching-quality.md records the sources. The parts that matter:

- Merge repeats of the same check into one habit. Six symptoms of one habit make a report
  look thorough and the player look hopeless; the research calls this symptom inflation.
- Cap the action set at three, preferring fewer. This is the best-supported number in the
  whole literature: feedback volume has a cost of its own, independent of quality.
- Rank by what costs rounds, weighting recurrence most heavily. Approximated from what
  sampled frames can actually see, which is not the full model (see COST_WEIGHTS).
- Corrections first, genuine praise after, never as a cushion. Positive-first-positive-last
  is the worst-performing order tested.
- Never pad praise to a ratio. The famous 5:1 traces to a withdrawn model.
- Finish with exactly one thing to practise, with a way to tell whether it worked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from round_review.coaching.knowledge import (
    Checklist,
    load_checklist,
    load_drills,
    render_rank_focus,
)
from round_review.coaching.parse import Finding, Strength
from round_review.coaching.review import WindowResult

MAX_FOCUS_ITEMS = 3
MAX_STRENGTHS = 3
# Below this, a single instance is not evidence of a habit worth a focus slot.
SINGLE_INSTANCE_CONFIDENCE = 0.7


@dataclass(frozen=True, slots=True)
class CategoryCost:
    """What a failure in this category costs, and how much of it the player controls.

    Scales follow the research: cost 0-3 (3 = loses the round or gives a free man), control
    1-2 (2 = fully the player's own decision), upstream 0-1 (1 = round-start or economy
    layer, where fixing it prevents the later problems).

    These stand in for per-round outcome data, which sampled frames cannot give us. That is
    the main approximation in this scoring and it is stated in every report.
    """

    cost: int
    control: int
    upstream: int


COST_WEIGHTS: dict[str, CategoryCost] = {
    "positioning": CategoryCost(cost=3, control=2, upstream=0),
    "peeking": CategoryCost(cost=3, control=2, upstream=0),
    "trading": CategoryCost(cost=3, control=1, upstream=0),
    "postplant": CategoryCost(cost=3, control=1, upstream=0),
    "retake": CategoryCost(cost=3, control=1, upstream=0),
    "utility": CategoryCost(cost=2, control=2, upstream=1),
    "timing": CategoryCost(cost=2, control=2, upstream=1),
    "info": CategoryCost(cost=2, control=2, upstream=0),
    "crosshair": CategoryCost(cost=2, control=2, upstream=0),
    "movement": CategoryCost(cost=2, control=2, upstream=0),
    "economy": CategoryCost(cost=1, control=2, upstream=1),
    "mental": CategoryCost(cost=1, control=2, upstream=0),
    "other": CategoryCost(cost=1, control=1, upstream=0),
}
DEFAULT_COST = CategoryCost(cost=1, control=1, upstream=0)

# Non-empty means the finding leans on something the player could not have known, so it is
# reported separately rather than counted against them.
PLACEHOLDER_LATER: frozenset[str] = frozenset({"", "none", "n/a", "na", "nothing", "-"})


@dataclass(frozen=True, slots=True)
class Habit:
    """One check, and every time it came up in this review."""

    check_id: str
    category: str
    label: str | None
    count: int
    windows: tuple[int, ...]
    instances: tuple[Finding, ...]
    mean_confidence: float
    score: int

    @property
    def lead(self) -> Finding:
        return self.instances[0]


@dataclass(frozen=True, slots=True)
class PracticeItem:
    for_check_id: str
    rule: str
    drill: str
    success_check: str


@dataclass(frozen=True, slots=True)
class SessionSummary:
    verdict: str
    focus: tuple[Habit, ...]
    strengths: tuple[Strength, ...]
    hindsight: tuple[Habit, ...]
    also_seen: tuple[Habit, ...]
    practice: PracticeItem | None
    rank_focus: str | None
    windows_reviewed: int


def recurrence_points(count: int, mean_confidence: float) -> int:
    """0-3, on the scale the coaching research uses: repetition is the strongest signal that
    something is a habit rather than an accident."""
    if count >= 4:
        return 3
    if count >= 2:
        return 2
    return 1 if mean_confidence >= SINGLE_INSTANCE_CONFIDENCE else 0


def _leans_on_hindsight(finding: Finding) -> bool:
    return finding.information_revealed_later.strip().rstrip(".").lower() not in PLACEHOLDER_LATER


def _score(category: str, count: int, mean_confidence: float) -> int:
    weights = COST_WEIGHTS.get(category, DEFAULT_COST)
    return (
        3 * recurrence_points(count, mean_confidence)
        + 3 * weights.cost
        + 2 * weights.control
        + weights.upstream
    )


def _habit(check_id: str, findings: Sequence[tuple[int, Finding]], checklist: Checklist) -> Habit:
    # The clearest instance leads, so the player has one moment to go and look at.
    ordered = sorted(findings, key=lambda pair: (-pair[1].confidence, pair[1].timestamp_s))
    instances = tuple(finding for _, finding in ordered)
    category = instances[0].category
    mean_confidence = sum(f.confidence for f in instances) / len(instances)
    check = checklist.find_check(check_id)
    return Habit(
        check_id=check_id,
        category=category,
        label=check.check if check else None,
        count=len(instances),
        windows=tuple(sorted({index for index, _ in findings})),
        instances=instances,
        mean_confidence=mean_confidence,
        score=_score(category, len(instances), mean_confidence),
    )


def _group(
    results: Sequence[WindowResult], hindsight: bool
) -> dict[str, list[tuple[int, Finding]]]:
    grouped: dict[str, list[tuple[int, Finding]]] = {}
    for result in results:
        for finding in result.findings:
            if _leans_on_hindsight(finding) is not hindsight:
                continue
            grouped.setdefault(finding.check_id, []).append((result.window.index, finding))
    return grouped


def _rank(habits: Sequence[Habit]) -> list[Habit]:
    # Alphabetical last so an identical score always produces the same report.
    return sorted(habits, key=lambda h: (-h.score, -h.count, h.check_id))


def _practice_for(habit: Habit) -> PracticeItem | None:
    practice = load_drills().for_category(habit.category)
    if practice is None:
        return None
    return PracticeItem(
        for_check_id=habit.check_id,
        rule=practice.rule,
        drill=practice.drill,
        success_check=(
            f"Next review: this should show up fewer than {habit.count} times. "
            "It came up " + ("once" if habit.count == 1 else f"{habit.count} times") + " here."
        ),
    )


def first_sentence(text: str, limit: int = 110) -> str:
    """The player-facing name for a habit: what the model saw, not the checklist question.

    Checklist text is written for the model ("does the player avoid re-peeking...?") and
    reads terribly as a heading. The observation is already a sentence about this player.
    """
    sentence = text.strip().split(". ")[0].rstrip(".")
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rsplit(" ", 1)[0] + "\u2026"
    return sentence


def _verdict(focus: Sequence[Habit], windows: int) -> str:
    if not focus:
        return (
            f"Nothing worth acting on came out of the {windows} reviewed "
            f"{'window' if windows == 1 else 'windows'}."
        )
    lead = focus[0]
    times = "once" if lead.count == 1 else f"{lead.count} times"
    return f"{first_sentence(lead.lead.observation)} ({lead.category}, {times})."


def build_session_summary(
    results: Sequence[WindowResult], rank: str | None, checklist: Checklist | None = None
) -> SessionSummary:
    """Merge, rank and cap the review into an action set, and choose one thing to practise."""
    checklist = checklist or load_checklist()

    habits = _rank(
        [_habit(check_id, found, checklist) for check_id, found in _group(results, False).items()]
    )
    hindsight = _rank(
        [_habit(check_id, found, checklist) for check_id, found in _group(results, True).items()]
    )
    focus = tuple(habits[:MAX_FOCUS_ITEMS])
    also_seen = tuple(habits[MAX_FOCUS_ITEMS:])

    # Strongest praise first, and never padded to a ratio: an empty list is a valid answer.
    strengths = tuple(
        sorted(
            (s for result in results for s in result.strengths),
            key=lambda s: (-s.confidence, s.timestamp_s),
        )[:MAX_STRENGTHS]
    )

    return SessionSummary(
        verdict=_verdict(focus, len(results)),
        focus=focus,
        strengths=strengths,
        hindsight=tuple(hindsight),
        also_seen=also_seen,
        practice=_practice_for(focus[0]) if focus else None,
        rank_focus=render_rank_focus(checklist, rank),
        windows_reviewed=len(results),
    )
