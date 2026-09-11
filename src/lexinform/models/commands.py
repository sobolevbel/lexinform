"""Operator commands from the technical channel: what the relay files into the inbox and how
the run reads a command line such as `/analyze 3039 force`.

A command names a bill by any number the bot knows (`3039`, `RPW/29075/2026`, `RCL/12414100`,
`UC164`, `RM-0610-139-26`) or by a link to the bill on sejm.gov.pl, api.sejm.gov.pl or
legislacja.rcl.gov.pl. Nothing here does I/O.
"""

import datetime as dt
import re
from enum import StrEnum
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field

from lexinform.models.analysis import TokenUsage
from lexinform.models.bill import Bill
from lexinform.models.enums import PRE_PRINT_PREFIX, RCL_PREFIX
from lexinform.models.rcl import normalize_wykaz_number


class IncomingCommand(BaseModel):
    """One channel post, as the relay stores it in the inbox (`inbox/{update_id}.json`)."""

    model_config = ConfigDict(frozen=True)

    update_id: int
    # Where the command was posted. The run answers in the technical channel it is configured
    # with (one relay, one channel), so this is provenance, not an address.
    chat_id: str
    message_id: int
    text: str
    received_at: dt.datetime


class CommandState(BaseModel):
    """What earlier runs did with a recorded command: ran it, answered it, and with what.

    Two marks, not one: a command whose answer never reached the channel must not be run a
    second time (`/republish` would post a second card), only answered again.
    """

    model_config = ConfigDict(frozen=True)

    executed_at: dt.datetime | None = None
    handled_at: dt.datetime | None = None
    reply: str | None = None


class ChannelPost(BaseModel):
    """One Telegram update as the relay sees it: a post in some channel (or chat)."""

    model_config = ConfigDict(frozen=True)

    update_id: int
    chat_id: int
    chat_username: str | None = None
    message_id: int
    text: str | None = None
    date: dt.datetime

    def is_from(self, channel: str) -> bool:
        """Whether the post comes from `channel`: a numeric id (`-100…`) or `@username`."""
        wanted = channel.strip()
        if wanted.startswith("@"):
            return (
                self.chat_username is not None and self.chat_username.lower() == wanted[1:].lower()
            )
        return str(self.chat_id) == wanted

    def as_command(self) -> IncomingCommand:
        assert self.text is not None
        return IncomingCommand(
            update_id=self.update_id,
            chat_id=str(self.chat_id),
            message_id=self.message_id,
            text=self.text,
            received_at=self.date,
        )


class RefKind(StrEnum):
    DRUK = "druk"  # a numbered print of a Sejm term
    RPW = "rpw"  # a bill in /bills without a print number yet
    RCL = "rcl"  # a government project on legislacja.rcl.gov.pl, by project id
    WYKAZ = "wykaz"  # the same, by its wykaz number (UC164)
    RM = "rm"  # the same, by the RM number the Sejm print carries (RM-0610-139-26)


class BillRef(BaseModel):
    """What a command points at; `term` only when the reference carries one (a Sejm link)."""

    model_config = ConfigDict(frozen=True)

    kind: RefKind
    value: str
    term: int | None = None

    @property
    def label(self) -> str:
        return f"druk {self.value}" if self.kind is RefKind.DRUK else self.value


class CommandName(StrEnum):
    ANALYZE = "analyze"
    SHOW = "show"
    SKIP = "skip"
    REPUBLISH = "republish"
    HELP = "help"


NEEDS_REFERENCE = frozenset(
    {CommandName.ANALYZE, CommandName.SHOW, CommandName.SKIP, CommandName.REPUBLISH}
)


class Command(BaseModel):
    """A parsed command line; `error` says what is wrong with it (the reply repeats it)."""

    model_config = ConfigDict(frozen=True)

    name: CommandName
    ref: BillRef | None = None
    force: bool = False  # analyse past the prefilter, a previous analysis and the cost guard
    publish: bool = False  # post the card of a relevant bill even below the score threshold
    error: str | None = None


class OutcomeStatus(StrEnum):
    ANALYSED = "analysed"  # a verdict (fresh or stored); `message_id` when the card went out
    SKIPPED = "skipped"  # the prefilter said no (`note` = why); `force` gets past it
    SHOWN = "shown"
    SILENCED = "silenced"  # /skip: the bill will not be analysed or posted
    REPUBLISHED = "republished"
    HELP = "help"
    EXECUTED_EARLIER = "executed earlier"  # ran in a previous run whose answer did not arrive
    NOT_FOUND = "not_found"
    ERROR = "error"


class CommandOutcome(BaseModel):
    """What happened to a command; the replier renders it under the command's message."""

    model_config = ConfigDict(frozen=True)

    status: OutcomeStatus
    bill: Bill | None = None
    note: str = ""  # the reason, the error, why the card was not posted
    message_id: int | None = None  # the card just posted (or posted again)
    # What running this command took: when the run that answered it started, how long the
    # command itself took, and the model tokens it spent (empty when the model was not called).
    run_started_at: dt.datetime | None = None
    seconds: float | None = None
    usage: dict[str, TokenUsage] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status not in (OutcomeStatus.ERROR, OutcomeStatus.NOT_FOUND)

    def line(self) -> str:
        """One row of the run report: the status and the note."""
        head = self.status.value
        if self.bill is not None:
            head = f"{self.bill.number} {head}"
        if self.message_id is not None:
            head += f" (message {self.message_id})"
        return f"{head}: {self.note}" if self.note else head


_DRUK = re.compile(r"^(?:druk\s*(?:nr\s*)?)?(\d{1,5})$", re.IGNORECASE)
_RPW = re.compile(r"^RPW/\d+/\d{4}$", re.IGNORECASE)
_RCL = re.compile(r"^RCL/(\d+)$", re.IGNORECASE)
_WYKAZ = re.compile(r"^U[A-Z]{1,4}\s?\d+$", re.IGNORECASE)
_RM = re.compile(r"^RM-\d{3,4}-\d+-\d{2}$", re.IGNORECASE)
_SEJM_TERM_PATH = re.compile(r"/Sejm(\d+)\.nsf/", re.IGNORECASE)
_API_PATH = re.compile(r"^/sejm/term(\d+)/(?:processes|prints)/(\d+)", re.IGNORECASE)
_RCL_PATH = re.compile(r"/projekt/(\d+)")
_MODIFIERS = {"force": "force", "--force": "force", "publish": "publish", "--publish": "publish"}


def parse_reference(text: str) -> BillRef | None:
    """A bill number in any of the bot's notations, or a link to the bill; None otherwise."""
    value = text.strip().strip("<>")
    if not value:
        return None
    if "gov.pl/" in value.lower():
        return _parse_url(value)
    if match := _DRUK.match(value):
        return BillRef(kind=RefKind.DRUK, value=str(int(match.group(1))))
    if _RPW.match(value):
        return BillRef(kind=RefKind.RPW, value=PRE_PRINT_PREFIX + value[len(PRE_PRINT_PREFIX) :])
    if match := _RCL.match(value):
        return BillRef(kind=RefKind.RCL, value=RCL_PREFIX + match.group(1))
    if _WYKAZ.match(value):
        return BillRef(kind=RefKind.WYKAZ, value=normalize_wykaz_number(value) or value)
    if _RM.match(value):
        return BillRef(kind=RefKind.RM, value=value.upper())
    return None


def _parse_url(value: str) -> BillRef | None:
    url = urlparse(value if "://" in value else f"https://{value}")
    host = (url.hostname or "").lower()
    query = parse_qs(url.query)
    if host.endswith("legislacja.rcl.gov.pl"):
        if match := _RCL_PATH.search(url.path):
            return BillRef(kind=RefKind.RCL, value=RCL_PREFIX + match.group(1))
        for rm in query.get("number", []):
            if _RM.match(rm):
                return BillRef(kind=RefKind.RM, value=rm.upper())
        return None
    if host == "api.sejm.gov.pl":
        if match := _API_PATH.match(url.path):
            return BillRef(
                kind=RefKind.DRUK, value=str(int(match.group(2))), term=int(match.group(1))
            )
        return None
    if host.endswith("sejm.gov.pl"):
        term_match = _SEJM_TERM_PATH.search(url.path)
        term = int(term_match.group(1)) if term_match else None
        for rpw in query.get("NrProjektu", []):
            if _RPW.match(rpw):
                return BillRef(kind=RefKind.RPW, value=rpw.upper(), term=term)
        for nr in query.get("nr", []):
            if nr.isdigit():
                return BillRef(kind=RefKind.DRUK, value=str(int(nr)), term=term)
        return None
    return None


def parse_command(text: str) -> Command | None:
    """The command in a channel post; None when the post is not a command (no leading slash).
    An unknown command, a missing or unreadable reference come back as `help` with `error`."""
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    head = words[0][1:].split("@", 1)[0].lower()  # `/analyze@lexinform_bot` in a group
    try:
        name = CommandName(head)
    except ValueError:
        return Command(name=CommandName.HELP, error=f"unknown command /{head}")
    force = publish = False
    args: list[str] = []
    for word in words[1:]:
        modifier = _MODIFIERS.get(word.lower())
        if modifier == "force":
            force = True
        elif modifier == "publish":
            publish = True
        else:
            args.append(word)
    if name not in NEEDS_REFERENCE:
        return Command(name=name)
    if not args:
        return Command(name=name, error=f"/{name} needs a bill number or a link")
    ref = parse_reference(" ".join(args))
    if ref is None:
        return Command(
            name=name, error=f"cannot read a bill number or a link in {' '.join(args)!r}"
        )
    return Command(name=name, ref=ref, force=force, publish=publish)
