"""Exceptions shared by adapters and services.

`ServiceUnavailableError` means an external system (Sejm API, LLM, Telegram) is down or
rejecting us as a whole. Services treat it as phase-fatal: stop the current phase, do not
burn per-bill retry attempts, report a clear message. Anything else is a per-bill problem.
"""


class ServiceUnavailableError(RuntimeError):
    system: str = "external service"

    def describe(self) -> str:
        return f"{self.system} unavailable: {self}"


class SejmApiUnavailableError(ServiceUnavailableError):
    system = "Sejm API"


class AttachmentTooLargeError(RuntimeError):
    """A download was stopped because the body exceeded the caller's limit (per-item problem)."""

    def __init__(self, url: str, limit: int) -> None:
        super().__init__(f"{url} exceeds {limit} bytes")
        self.url = url
        self.limit = limit


class LlmUnavailableError(ServiceUnavailableError):
    system = "LLM API"


class TelegramUnavailableError(ServiceUnavailableError):
    system = "Telegram API"
