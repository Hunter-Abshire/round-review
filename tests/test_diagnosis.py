"""The review has to say when it produced nothing because the model misread the screen."""

from round_review.coaching.review import WindowResult
from round_review.diagnosis import abstention_warning
from round_review.video.windows import Window


def window(index: int) -> Window:
    return Window(index, index * 12.0, index * 12.0 + 12.0, "tiled")


def abstained(index: int, reason: str) -> WindowResult:
    return WindowResult(
        window=window(index),
        samples=(),
        findings=(),
        strengths=(),
        model_calls=1,
        warnings=(),
        abstained_reason=reason,
    )


def coached(index: int, findings: int = 1) -> WindowResult:
    from round_review.coaching.parse import Finding

    made = tuple(
        Finding(
            index * 12.0 + 1,
            "crosshair.head_level",
            "crosshair",
            "o",
            "v",
            "i",
            "",
            (),
            "s",
            0.6,
            None,
        )
        for _ in range(findings)
    )
    return WindowResult(
        window=window(index),
        samples=(),
        findings=made,
        strengths=(),
        model_calls=2,
        warnings=(),
    )


coached_window = coached


def test_no_warning_when_the_review_produced_findings() -> None:
    assert abstention_warning([coached(0), abstained(1, "buy phase")]) is None


def test_no_warning_when_nothing_abstained() -> None:
    coached_nothing = WindowResult(
        window=window(0), samples=(), findings=(), strengths=(), model_calls=2, warnings=()
    )
    assert abstention_warning([coached_nothing]) is None


def test_no_warning_for_an_empty_review() -> None:
    assert abstention_warning([]) is None


def test_warns_when_every_window_abstained_and_nothing_was_found() -> None:
    results = [abstained(i, "buy phase") for i in range(6)] + [
        abstained(6, "round phase unreadable"),
        abstained(7, "round phase unreadable"),
    ]
    warning = abstention_warning(results)
    assert warning is not None
    assert "8 of 8" in warning
    assert "6 buy phase" in warning
    assert "2 round phase unreadable" in warning
    assert "misreading" in warning
    assert "scenes validate" in warning


def test_warns_when_abstentions_dominate_even_with_a_finding() -> None:
    results = [abstained(i, "buy phase") for i in range(7)] + [coached(7)]
    warning = abstention_warning(results)
    assert warning is not None
    assert "7 of 8" in warning


def test_counts_reasons_in_descending_order() -> None:
    results = [
        abstained(0, "round phase unreadable"),
        abstained(1, "buy phase"),
        abstained(2, "buy phase"),
    ]
    warning = abstention_warning(results)
    assert warning is not None
    assert warning.index("2 buy phase") < warning.index("1 round phase unreadable")


def test_spectating_alone_is_not_a_misread_diagnosis() -> None:
    # Spectating really does mean there is nothing of the player's to coach.
    results = [abstained(i, "spectating another player") for i in range(4)]
    warning = abstention_warning(results)
    assert warning is not None
    assert "spectating" in warning
    assert "misreading" not in warning


class TestASkippedButProductiveReview:
    """13 of 21 windows skipped while 22 findings came out is not a broken review."""

    def results(self, coached: int, skipped: int, findings_each: int = 3) -> list[WindowResult]:
        out = [coached_window(i, findings_each) for i in range(coached)]
        out += [abstained(coached + i, "buy phase") for i in range(skipped)]
        return out

    def test_a_review_with_real_findings_is_not_called_empty(self) -> None:
        warning = abstention_warning(self.results(coached=8, skipped=13))
        assert warning is not None
        assert "misreading" not in warning
        assert "nothing to say" not in warning
        # it still says what was skipped, because that is worth knowing
        assert "13 of 21" in warning
        assert "buy phase" in warning

    def test_an_empty_review_still_gets_the_full_diagnosis(self) -> None:
        warning = abstention_warning([abstained(i, "buy phase") for i in range(8)])
        assert warning is not None
        assert "misreading the screen" in warning
        assert "scenes validate" in warning

    def test_a_barely_productive_review_is_still_flagged(self) -> None:
        # one finding out of twenty windows is not a working review either
        warning = abstention_warning(self.results(coached=1, skipped=19, findings_each=1))
        assert warning is not None
        assert "misreading" in warning

    def test_a_review_with_few_skips_says_nothing(self) -> None:
        assert abstention_warning(self.results(coached=18, skipped=3)) is None
