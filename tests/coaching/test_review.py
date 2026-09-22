import json
from collections import deque
from pathlib import Path

import pytest

from round_review.coaching.context import PlayerContext
from round_review.coaching.knowledge import load_knowledge
from round_review.coaching.review import WindowResult, review_window
from round_review.errors import CapExceeded, ParseError
from round_review.llm.transport import ChatRequest, ChatResponse
from round_review.video.frames import FrameSample
from round_review.video.windows import Window
from round_review.vision.hud import HudRead

WINDOW = Window(index=0, start_s=60.0, end_s=72.0, source="evenly_spaced")


class FakeTransport:
    def __init__(self, *contents: str) -> None:
        self.responses = deque(contents)
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(content=self.responses.popleft(), prompt_eval_count=0, eval_count=0)


SITUATION = json.dumps(
    {
        "agent": "Jett",
        "map": "Ascent",
        "side": "attack",
        "phase": "early",
        "weapon": "Vandal",
        "abilities_available": ["Tailwind"],
        "credits": 3900,
        "teammates_alive": 4,
        "enemies_visible": 0,
        "timeline": [{"t": 60.0, "event": "walking A main"}],
        "summary": "Entering A main with dash up.",
    }
)
KNOWLEDGE = load_knowledge()


def review(
    transport: FakeTransport, samples: list[FrameSample], **overrides: object
) -> WindowResult:
    kwargs: dict[str, object] = {
        "model": "m",
        "calls_today": 0,
        "cap": 10,
        "timeout_s": 1.0,
        "context": PlayerContext(rank="Gold 2"),
        "knowledge": KNOWLEDGE,
        "situation_pass": True,
    }
    kwargs.update(overrides)
    return review_window(WINDOW, samples, transport, **kwargs)  # type: ignore[arg-type]


GOOD = json.dumps(
    {
        "findings": [
            {
                "timestamp_s": 61.0,
                "check_id": "utility.has_purpose",
                "category": "utility",
                "observation": "o",
                "visible_evidence": "v",
                "information_available_to_player": "i",
                "information_revealed_later": "",
                "assumption_flags": [],
                "suggested_alternative": "s",
                "confidence": 0.6,
            }
        ]
    }
)


@pytest.fixture
def samples(tmp_path: Path) -> list[FrameSample]:
    out = []
    for i in range(3):
        p = tmp_path / f"w00_{i:03d}.jpg"
        p.write_bytes(b"jpg")
        out.append(FrameSample(0, 60.0 + i, p))
    return out


def test_two_pass_happy_path(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    result = review(transport, samples)
    assert isinstance(result, WindowResult)
    assert result.model_calls == 2
    assert len(result.findings) == 1
    assert result.findings[0].check_id == "utility.has_purpose"
    assert result.warnings == ()
    assert result.situation is not None and result.situation.agent == "Jett"
    # detected agent/map merged into the context the coach pass saw; user rank kept
    assert result.context == PlayerContext(rank="Gold 2", agent="Jett", map="Ascent", side="attack")
    assert len(transport.calls[0].images_b64) == 3
    assert "Agent brief: Jett" in transport.calls[1].prompt
    assert "Map brief: Ascent" in transport.calls[1].prompt
    assert "Entering A main with dash up." in transport.calls[1].prompt
    assert transport.calls[1].model == "m"


def test_situation_pass_can_be_disabled(samples: list[FrameSample]) -> None:
    transport = FakeTransport(GOOD)
    result = review(transport, samples, situation_pass=False)
    assert result.model_calls == 1
    assert result.situation is None
    assert result.context == PlayerContext(rank="Gold 2")


def test_unparseable_situation_is_a_warning_not_a_failure(samples: list[FrameSample]) -> None:
    transport = FakeTransport("I see a video game.", GOOD)
    result = review(transport, samples)
    assert result.model_calls == 2
    assert result.situation is None
    assert len(result.findings) == 1
    assert any("situation" in w.lower() for w in result.warnings)


def test_retries_coach_pass_once_with_nudge_then_succeeds(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, "I cannot help with that.", GOOD)
    result = review(transport, samples)
    assert result.model_calls == 3
    assert len(result.findings) == 1
    assert "json" in transport.calls[2].prompt.lower()
    assert any("retry" in w.lower() for w in result.warnings)


def test_second_coach_failure_raises_parse_error_with_calls(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, "nope", "still nope")
    with pytest.raises(ParseError) as info:
        review(transport, samples)
    assert info.value.model_calls == 3
    assert len(transport.calls) == 3


def test_cap_is_enforced_across_passes(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    with pytest.raises(CapExceeded):
        review(transport, samples, calls_today=9, cap=10)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("phase", ["pre_round", "spectating", "unknown"])
def test_noncombat_or_unreadable_phase_abstains_without_coach_call(samples, phase) -> None:
    situation = {**json.loads(SITUATION), "phase": phase, "weapon": "Knife"}
    transport = FakeTransport(json.dumps(situation))
    result = review(transport, samples)
    assert result.findings == ()
    assert result.model_calls == 1
    assert result.warnings and "coaching skipped" in result.warnings[0]
    if phase != "unknown":
        assert result.situation is not None and result.situation.phase == phase


@pytest.mark.parametrize("weapon", ["Knife", "Melee", "unknown"])
def test_crosshair_findings_require_a_known_firearm(samples, weapon) -> None:
    situation = {**json.loads(SITUATION), "weapon": weapon}
    aim = {
        **json.loads(GOOD)["findings"][0],
        "check_id": "crosshair.head_level",
        "category": "crosshair",
    }
    transport = FakeTransport(json.dumps(situation), json.dumps({"findings": [aim]}))
    result = review(transport, samples)
    assert result.findings == ()
    assert any("crosshair" in w for w in result.warnings)


def test_phase_inappropriate_check_is_rejected_even_if_category_is_mislabeled(samples) -> None:
    finding = {
        **json.loads(GOOD)["findings"][0],
        "check_id": "postplant.spike_vision",
        "category": "utility",
    }
    result = review(FakeTransport(SITUATION, json.dumps({"findings": [finding]})), samples)
    assert result.findings == ()
    assert any("phase" in w for w in result.warnings)


def test_abstention_is_not_a_parse_failure(samples: list[FrameSample]) -> None:
    buy_phase = json.loads(SITUATION)
    buy_phase["phase"] = "pre_round"
    transport = FakeTransport(json.dumps(buy_phase))
    result = review(transport, samples)
    assert result.findings == ()
    assert result.parse_failed is False
    assert any("coaching skipped" in w for w in result.warnings)
    assert len(transport.calls) == 1  # no coach call spent on buy phase


def test_unparseable_coach_output_sets_parse_failed_on_the_raised_error(
    samples: list[FrameSample],
) -> None:
    transport = FakeTransport(SITUATION, "nope", "still nope")
    with pytest.raises(ParseError) as info:
        review(transport, samples)
    assert info.value.model_calls == 3


def test_successful_review_is_not_marked_parse_failed(samples: list[FrameSample]) -> None:
    result = review(FakeTransport(SITUATION, GOOD), samples)
    assert result.parse_failed is False


def test_abstention_records_a_machine_readable_reason(samples: list[FrameSample]) -> None:
    buy_phase = json.loads(SITUATION)
    buy_phase["phase"] = "pre_round"
    result = review(FakeTransport(json.dumps(buy_phase)), samples)
    assert result.abstained_reason == "buy phase"
    assert result.abstained is True


def test_unreadable_phase_abstains_with_its_own_reason(samples: list[FrameSample]) -> None:
    unreadable = json.loads(SITUATION)
    unreadable["phase"] = "unknown"
    result = review(FakeTransport(json.dumps(unreadable)), samples)
    assert result.abstained_reason == "round phase unreadable"


def test_a_coached_window_is_not_marked_abstained(samples: list[FrameSample]) -> None:
    result = review(FakeTransport(SITUATION, GOOD), samples)
    assert result.abstained is False
    assert result.abstained_reason is None


def hud_live(clock: str = "1:39", seconds: float = 99.0, confidence: float = 0.95) -> HudRead:
    return HudRead(clock, seconds, confidence, 4)


def test_hud_clock_overrides_a_buy_phase_misread(samples: list[FrameSample]) -> None:
    buy_phase = json.loads(SITUATION)
    buy_phase["phase"] = "pre_round"
    transport = FakeTransport(json.dumps(buy_phase), GOOD)
    result = review(transport, samples, hud=hud_live())

    assert result.abstained is False  # the window is coached instead of skipped
    assert len(result.findings) == 1
    assert result.situation is not None and result.situation.phase == "early"
    assert any("HUD round timer reads 1:39" in w for w in result.warnings)
    assert result.hud_override is True


def test_hud_rescues_an_unreadable_phase(samples: list[FrameSample]) -> None:
    unreadable = json.loads(SITUATION)
    unreadable["phase"] = "unknown"
    result = review(FakeTransport(json.dumps(unreadable), GOOD), samples, hud=hud_live())
    assert result.abstained is False
    assert result.situation is not None and result.situation.phase == "early"


def test_hud_does_not_override_spectating(samples: list[FrameSample]) -> None:
    spectating = json.loads(SITUATION)
    spectating["phase"] = "spectating"
    result = review(FakeTransport(json.dumps(spectating)), samples, hud=hud_live())
    assert result.abstained is True
    assert result.abstained_reason == "spectating another player"
    assert result.hud_override is False


def test_a_short_hud_clock_leaves_the_buy_phase_call_alone(samples: list[FrameSample]) -> None:
    buy_phase = json.loads(SITUATION)
    buy_phase["phase"] = "pre_round"
    result = review(FakeTransport(json.dumps(buy_phase)), samples, hud=hud_live("0:20", 20.0))
    assert result.abstained is True
    assert result.hud_override is False


def test_an_unread_hud_changes_nothing(samples: list[FrameSample]) -> None:
    buy_phase = json.loads(SITUATION)
    buy_phase["phase"] = "pre_round"
    result = review(FakeTransport(json.dumps(buy_phase)), samples, hud=HudRead(None, None, 0.0, 0))
    assert result.abstained is True


def test_the_situation_pass_carries_only_its_frame_budget(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    review(transport, samples, situation_frames=2)
    # pass 1 gets the budget, pass 2 still gets everything the coach needs
    assert len(transport.calls[0].images_b64) == 2
    assert len(transport.calls[1].images_b64) == 3


def test_a_budget_of_zero_sends_every_frame(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    review(transport, samples, situation_frames=0)
    assert len(transport.calls[0].images_b64) == 3


def test_the_situation_prompt_describes_the_frames_it_was_given(
    samples: list[FrameSample],
) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    review(transport, samples, situation_frames=2)
    prompt = transport.calls[0].prompt
    assert "2 frames" in prompt
    assert prompt.count("t=") == 2 + 2  # two captions plus the window bounds


def test_the_coach_pass_can_be_given_its_own_frame_budget(samples: list[FrameSample]) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    review(transport, samples, situation_frames=1, coach_frames=2)
    assert len(transport.calls[0].images_b64) == 1
    assert len(transport.calls[1].images_b64) == 2
    assert "2 frames" in transport.calls[1].prompt


GOOD_WITH_STRENGTH = json.dumps(
    {
        "findings": json.loads(GOOD)["findings"],
        "strengths": [
            {
                "timestamp_s": 62.0,
                "check_id": "utility.has_purpose",
                "category": "utility",
                "observation": "Threw the smoke to cover the plant rather than to cross a gap.",
                "visible_evidence": "At t=62.0s the smoke lands on the default plant spot.",
                "why_it_worked": "It denied the retake angle while the spike went down.",
                "confidence": 0.75,
            }
        ],
    }
)


def test_strengths_come_back_with_the_findings(samples: list[FrameSample]) -> None:
    result = review(FakeTransport(SITUATION, GOOD_WITH_STRENGTH), samples)
    assert len(result.findings) == 1
    (strength,) = result.strengths
    assert strength.category == "utility"
    assert strength.evidence_frame is not None


def test_a_window_with_no_strengths_is_fine(samples: list[FrameSample]) -> None:
    assert review(FakeTransport(SITUATION, GOOD), samples).strengths == ()


def _situation(**overrides: object) -> str:
    base = json.loads(SITUATION)
    base.update(overrides)
    return json.dumps(base)


def _coach(category: str, check_id: str, observation: str = "o") -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "timestamp_s": 61.0,
                    "check_id": check_id,
                    "category": category,
                    "observation": observation,
                    "visible_evidence": "v",
                    "information_available_to_player": "i",
                    "information_revealed_later": "",
                    "assumption_flags": ["enemy position"],
                    "suggested_alternative": "s",
                    "confidence": 0.8,
                }
            ]
        }
    )


def test_trade_distance_is_dropped_when_no_enemy_is_on_screen(
    samples: list[FrameSample],
) -> None:
    """Being far from a teammate while crossing an empty map is not an untradeable death."""
    transport = FakeTransport(
        _situation(enemies_visible=0), _coach("trading", "trading.stay_tradeable")
    )
    result = review(transport, samples)
    assert result.findings == ()
    assert any("no enemy" in w.lower() for w in result.warnings)


def test_trade_distance_survives_when_an_enemy_is_visible(samples: list[FrameSample]) -> None:
    transport = FakeTransport(
        _situation(enemies_visible=1), _coach("trading", "trading.stay_tradeable")
    )
    assert len(review(transport, samples).findings) == 1


def test_other_categories_are_untouched_by_that_rule(samples: list[FrameSample]) -> None:
    transport = FakeTransport(
        _situation(enemies_visible=0), _coach("crosshair", "crosshair.head_level")
    )
    assert len(review(transport, samples).findings) == 1


def test_without_a_situation_read_nothing_is_dropped(samples: list[FrameSample]) -> None:
    transport = FakeTransport(_coach("trading", "trading.stay_tradeable"))
    assert len(review(transport, samples, situation_pass=False).findings) == 1


def _situation(**overrides: object) -> str:
    base = json.loads(SITUATION)
    base.update(overrides)
    return json.dumps(base)


def _findings(**overrides: object) -> str:
    base = json.loads(GOOD)
    base["findings"][0].update(overrides)
    return json.dumps(base)


def test_user_agent_overrides_a_misread_portrait(samples: list[FrameSample]) -> None:
    transport = FakeTransport(_situation(agent="Omen"), GOOD)
    result = review(transport, samples, context=PlayerContext(agent="Veto"))
    assert result.situation is not None
    # One story in the prompt: the brief and the situation read must not disagree.
    assert result.situation.agent == "Veto"
    assert "agent=Veto" in transport.calls[1].prompt
    assert "agent=Omen" not in transport.calls[1].prompt
    assert any("Veto" in w and "Omen" in w for w in result.warnings)


def test_situation_agent_is_kept_when_the_user_gave_none(samples: list[FrameSample]) -> None:
    transport = FakeTransport(_situation(agent="Omen"), GOOD)
    result = review(transport, samples)
    assert result.situation is not None and result.situation.agent == "Omen"


def test_finding_naming_another_agents_ability_is_dropped(samples: list[FrameSample]) -> None:
    transport = FakeTransport(
        _situation(agent="Veto"),
        _findings(suggested_alternative="Use Shrouded Step to take the off-angle."),
    )
    result = review(transport, samples, context=PlayerContext(agent="Veto"))
    assert result.findings == ()
    assert any("Shrouded Step" in w for w in result.warnings)


def test_finding_naming_the_players_own_ability_survives(samples: list[FrameSample]) -> None:
    transport = FakeTransport(
        _situation(agent="Veto"),
        _findings(suggested_alternative="Chokehold the choke before you push."),
    )
    result = review(transport, samples, context=PlayerContext(agent="Veto"))
    assert len(result.findings) == 1


def test_a_decided_round_is_not_coached(samples: list[FrameSample]) -> None:
    # 5 alive against 1, nobody on screen: standing still is running the clock down, not
    # a positioning mistake.
    transport = FakeTransport(
        _situation(teammates_alive=4, enemies_alive=1, enemies_visible=0),
        _findings(check_id="positioning.off_angle_static", category="positioning"),
    )
    result = review(transport, samples)
    assert result.findings == ()
    assert any("advantage" in w.lower() for w in result.warnings)


def test_an_even_round_is_still_coached(samples: list[FrameSample]) -> None:
    transport = FakeTransport(
        _situation(teammates_alive=4, enemies_alive=4, enemies_visible=0),
        _findings(check_id="positioning.off_angle_static", category="positioning"),
    )
    result = review(transport, samples)
    assert len(result.findings) == 1


def test_a_visible_enemy_keeps_coaching_alive_despite_the_advantage(
    samples: list[FrameSample],
) -> None:
    transport = FakeTransport(
        _situation(teammates_alive=4, enemies_alive=1, enemies_visible=1),
        _findings(check_id="positioning.off_angle_static", category="positioning"),
    )
    result = review(transport, samples)
    assert len(result.findings) == 1


def test_utility_advice_is_dropped_when_the_hud_says_nothing_was_up(
    samples: list[FrameSample],
) -> None:
    from round_review.vision.state import HudState

    transport = FakeTransport(SITUATION, GOOD)  # GOOD is a utility finding
    result = review(transport, samples, state=HudState(100, 3900, (False, False, False), ()))
    assert result.findings == ()
    assert any("no ability was available" in w for w in result.warnings)


def test_utility_advice_survives_when_an_ability_was_up(samples: list[FrameSample]) -> None:
    from round_review.vision.state import HudState

    transport = FakeTransport(SITUATION, GOOD)
    result = review(transport, samples, state=HudState(100, 3900, (True, False, False), ()))
    assert len(result.findings) == 1


def test_an_unread_hud_does_not_gate_anything(samples: list[FrameSample]) -> None:
    from round_review.vision.state import HudState

    transport = FakeTransport(SITUATION, GOOD)
    result = review(transport, samples, state=HudState(None, None, (), ()))
    assert len(result.findings) == 1


def test_measured_hud_values_reach_the_coach_prompt(samples: list[FrameSample]) -> None:
    from round_review.vision.state import HudState

    transport = FakeTransport(SITUATION, GOOD)
    review(transport, samples, state=HudState(48, 2400, (True, False), ()))
    prompt = transport.calls[1].prompt
    assert "health=48" in prompt and "credits=2400" in prompt
    assert "abilities up=1 of 2" in prompt


def test_a_corrected_phase_is_stated_over_the_models_own_summary(
    samples: list[FrameSample],
) -> None:
    """The clock overruled the model, but its summary still said "buy phase" and that text
    went to the coach. One finding opened with "The player is in the Buy Phase" on a window
    the clock had already proven was live."""
    buy = json.loads(_situation(phase="pre_round"))
    buy["summary"] = "The player is in the buy phase behind the barrier."
    transport = FakeTransport(json.dumps(buy), GOOD)
    result = review(transport, samples, hud=HudRead("1:34", 94.0, 1.0, 4))
    prompt = transport.calls[1].prompt
    assert result.hud_override
    assert "1:34" in prompt
    assert "not the buy phase" in prompt.lower() or "is live" in prompt.lower()


def test_no_correction_is_announced_when_the_model_was_right(
    samples: list[FrameSample],
) -> None:
    transport = FakeTransport(SITUATION, GOOD)
    result = review(transport, samples, hud=HudRead("1:34", 94.0, 1.0, 4))
    assert not result.hud_override
    assert "corrected" not in transport.calls[1].prompt.lower()
