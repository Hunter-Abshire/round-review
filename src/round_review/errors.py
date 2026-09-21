"""Exception hierarchy. Every failure surfaced to the CLI or ledger is one of these."""


class RoundReviewError(Exception):
    """Base class for all round-review errors."""


class ConfigError(RoundReviewError):
    """Configuration file or environment override is invalid."""


class VideoError(RoundReviewError):
    """ffprobe/ffmpeg failed or produced unusable output."""


class OllamaError(RoundReviewError):
    """The Ollama HTTP transport failed (connection, timeout, non-2xx)."""


class ParseError(RoundReviewError):
    """The model response could not be parsed into findings.

    `model_calls` carries how many calls were spent before giving up, so the ledger can
    still count them against the daily cap.
    """

    def __init__(self, message: str, model_calls: int = 0) -> None:
        super().__init__(message)
        self.model_calls = model_calls


class CapExceeded(RoundReviewError):
    """The daily model-call cap has been reached."""


class LedgerError(RoundReviewError):
    """The ledger file is unreadable or corrupt."""


class AlreadyProcessed(RoundReviewError):
    """The recording is already in the ledger; pass force=True to review it again."""


class KnowledgeError(RoundReviewError):
    """A bundled knowledge file (checklist, agents, maps) is malformed."""


class LabelsError(RoundReviewError):
    """A scene-validation labels file is missing, malformed, or has nothing labelled."""


class HudError(RoundReviewError):
    """A HUD raster could not be read, or its digit templates are unusable."""
