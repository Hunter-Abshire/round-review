import round_review
from round_review import errors


def test_version_is_set() -> None:
    assert round_review.__version__ == "0.1.0"


def test_error_hierarchy() -> None:
    for cls in (
        errors.ConfigError,
        errors.VideoError,
        errors.OllamaError,
        errors.ParseError,
        errors.CapExceeded,
        errors.LedgerError,
    ):
        assert issubclass(cls, errors.RoundReviewError)
        assert issubclass(cls, Exception)
