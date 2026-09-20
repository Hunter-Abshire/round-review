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
    """The model response could not be parsed into findings."""


class CapExceeded(RoundReviewError):
    """The daily model-call cap has been reached."""


class LedgerError(RoundReviewError):
    """The ledger file is unreadable or corrupt."""
