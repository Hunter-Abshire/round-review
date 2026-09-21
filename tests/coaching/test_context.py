from round_review.coaching.context import PlayerContext, context_from_mapping, merge_context


def test_from_mapping_normalises_and_drops_blanks() -> None:
    ctx = context_from_mapping(
        {
            "rank": " Gold 2 ",
            "agent": "KAY/O",
            "map": "",
            "side": "Attack",
            "focus": None,
            "notes": "x",
        }
    )
    assert ctx == PlayerContext(
        rank="Gold 2", agent="KAY/O", map=None, side="attack", focus=None, notes="x"
    )


def test_from_mapping_rejects_unknown_side() -> None:
    assert context_from_mapping({"side": "spectator"}).side is None


def test_from_empty_mapping_is_empty_context() -> None:
    assert context_from_mapping({}) == PlayerContext()
    assert context_from_mapping(None) == PlayerContext()
    assert PlayerContext().is_empty()
    assert not PlayerContext(rank="Gold").is_empty()


def test_merge_prefers_user_values_over_detected() -> None:
    user = PlayerContext(agent="Jett", rank="Gold 2")
    detected = PlayerContext(agent="Reyna", map="Ascent", side="defense")
    merged = merge_context(user, detected)
    assert merged == PlayerContext(rank="Gold 2", agent="Jett", map="Ascent", side="defense")


def test_describe() -> None:
    assert PlayerContext().describe() == ""
    assert PlayerContext(
        rank="Gold 2", agent="Jett", map="Ascent", side="attack", focus="entries"
    ).describe() == ("Rank: Gold 2. Agent: Jett. Map: Ascent. Side: attack. Focus: entries.")
