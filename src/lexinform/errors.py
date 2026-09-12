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


class RclUnavailableError(ServiceUnavailableError):
    """legislacja.rcl.gov.pl is down, or its WAF answered "Request Rejected" to our request."""

    system = "RCL"


class WykazUnavailableError(ServiceUnavailableError):
    """gov.pl does not answer, so the wykaz prac legislacyjnych RM cannot be read."""

    system = "wykaz prac RM"


class OrkaUnreachableError(RuntimeError):
    """orka.sejm.gov.pl did not hand over the file — deliberately a per-bill problem.

    The host is fronted by Imperva, which judges a client by its address as well as its identity
    and can start refusing us without anything changing on our side. Everything else the analysis
    reads comes from api.sejm.gov.pl, so as a `ServiceUnavailableError` one WAF decision would
    stop the whole analysis phase, every run. A bill whose file cannot be read falls back to the
    analysis of its official description — what every bill without a print number got before this
    host was reachable at all.
    """


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
