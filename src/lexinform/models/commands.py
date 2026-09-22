"""Operator commands from the technical channel: what the relay files into the inbox and how
the run reads a command line such as `/analyze 3039 force`.

A command names a bill by any number the bot knows (`3039`, `RPW/29075/2026`, `RCL/12414100`,
`UC164`, `RM-0610-139-26`) or by a link to the bill on sejm.gov.pl, api.sejm.gov.pl or
legislacja.rcl.gov.pl. Nothing here does I/O.
"""

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field

from lexinform.models.analysis import TokenUsage
from lexinform.models.bill import Bill, Publication
from lexinform.models.digest import week_bounds
from lexinform.models.enums import PRE_PRINT_PREFIX, RCL_PREFIX, WYKAZ_PREFIX, BillStatus
from lexinform.models.rcl import normalize_wykaz_number
from lexinform.models.report import RunReport
from lexinform.models.sejm import PrintInfo


class IncomingCommand(BaseModel):
    """One channel post, as the relay stores it in the inbox (`inbox/{update_id}.json`).

    `chat_id` is where the command was posted. The run answers in the technical channel it is
    configured with — one relay, one channel — so it is provenance, not an address.
    """

    model_config = ConfigDict(frozen=True)

    update_id: int
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

    received_at: dt.datetime | None = None
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
    callback_id: str | None = None
    """A button press rather than a post: `text` is the command it stands for."""

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


CALLBACKS: dict[str, str] = {"digest": "/digest publish ref={}"}
"""What a pressed button stands for, by the prefix of its callback data: a button is a command
the operator did not have to type, and it takes the same road as one that was."""


def command_for_callback(data: str) -> str | None:
    """`digest:2026-W38` -> `/digest publish ref=2026-W38`; None for data no button of ours sent."""
    prefix, _, argument = data.partition(":")
    template = CALLBACKS.get(prefix)
    if template is None or not argument:
        return None
    return template.format(argument)


class RefKind(StrEnum):
    """Which number a command used to name a bill: a numbered print of a Sejm term, a bill in
    `/bills` that has none yet, or a government project — by its RCL project id, by its wykaz
    number (UC164) or by the RM number the Sejm print carries (RM-0610-139-26)."""

    DRUK = "druk"
    RPW = "rpw"
    RCL = "rcl"
    WYKAZ = "wykaz"
    RM = "rm"


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
    """Every `lexinform` subcommand the technical channel can reach, under its own CLI name.

    Three are missing on purpose: `listen` is the relay reading this channel, `commands` is the
    phase that answers what is written in it, and `db` is the workflow's handling of the state
    branch around every run — none of them is a thing to ask a run for.
    """

    RUN = "run"
    SCAN = "scan"
    TRACK = "track"
    COLLECT_BATCHES = "collect-batches"
    REPREFILTER = "reprefilter"
    INDEX_RCL_NUMBERS = "index-rcl-numbers"
    ANALYZE = "analyze"
    SHOW = "show"
    SKIP = "skip"
    UNSKIP = "unskip"
    RESET = "reset"
    REPUBLISH = "republish"
    FORGET = "forget"
    PREVIEW = "preview"
    REFRESH = "refresh"
    FIND = "find"
    RUNS = "runs"
    COST = "cost"
    STATUS = "status"
    DIGEST = "digest"
    HELP = "help"


NEEDS_REFERENCE = frozenset(
    {
        CommandName.ANALYZE,
        CommandName.SHOW,
        CommandName.SKIP,
        CommandName.UNSKIP,
        CommandName.RESET,
        CommandName.REPUBLISH,
        CommandName.FORGET,
        CommandName.PREVIEW,
        CommandName.REFRESH,
    }
)
NEEDS_QUERY = frozenset({CommandName.FIND})
MIN_QUERY_CHARS = 3

DISPATCHED = frozenset(
    {
        CommandName.RUN,
        CommandName.SCAN,
        CommandName.TRACK,
        CommandName.COLLECT_BATCHES,
        CommandName.REPREFILTER,
        CommandName.INDEX_RCL_NUMBERS,
    }
)
"""The commands no run executes, because each *is* a run: the relay starts `daily.yml` on them
instead of filing them, and an inbox file naming one is answered rather than obeyed."""

_COMMAND_NAMES: dict[str, CommandName] = {name.value: name for name in CommandName} | {
    "index": CommandName.INDEX_RCL_NUMBERS
}


class OptionKind(StrEnum):
    """A bare word (`force`), or `key=value` checked as a date, count, chat, status or ISO week."""

    FLAG = "flag"
    DATE = "date"
    COUNT = "count"
    CHAT = "chat"
    STATUS = "status"
    WEEK = "week"


@dataclass(frozen=True)
class Option:
    """One option of one command: what it is called here and what it becomes.

    `input` is a named input of `daily.yml`, `flag` a flag of the CLI command the workflow runs;
    an option with neither is read by the run that executes the command.
    """

    name: str
    kind: OptionKind = OptionKind.FLAG
    input: str | None = None
    flag: str | None = None
    low: int = 0
    high: int = 1_000_000


_SINCE = Option("since", OptionKind.DATE, input="since")
_DRY = Option("dry_run", OptionKind.FLAG, input="dry_run")
_REPREFILTER = Option("reprefilter_limit", OptionKind.COUNT, input="reprefilter_limit")
_TEXT_SKIPPED = Option(
    "reprefilter_text_skipped", OptionKind.FLAG, input="reprefilter_text_skipped"
)
_INDEX_SINCE = Option("index_rcl_since", OptionKind.DATE, input="index_rcl_since")
_SCAN_SINCE = Option("since", OptionKind.DATE, flag="--since")
_LIMIT = Option("limit", OptionKind.COUNT, flag="--limit", low=1)
_INCLUDE_TEXT_SKIPPED = Option("include_text_skipped", flag="--include-text-skipped")
_DAYS = Option("days", OptionKind.COUNT, low=1, high=3650)

OPTIONS: dict[CommandName, dict[str, Option]] = {
    CommandName.RUN: {
        "since": _SINCE,
        "dry": _DRY,
        "dry_run": _DRY,
        "reprefilter": _REPREFILTER,
        "reprefilter_limit": _REPREFILTER,
        "text_skipped": _TEXT_SKIPPED,
        "reprefilter_text_skipped": _TEXT_SKIPPED,
        "index": _INDEX_SINCE,
        "index_rcl_since": _INDEX_SINCE,
        "no_publish": Option("no_publish", flag="--no-publish"),
        "no_track": Option("no_track", flag="--no-track"),
        "no_rcl": Option("no_rcl", flag="--no-rcl"),
        "full_track": Option("full_track", flag="--full-track"),
        "max_publish": Option("max_publish", OptionKind.COUNT, flag="--max-publish"),
        "max_analyze": Option("max_analyze", OptionKind.COUNT, flag="--max-analyze"),
        "min_score": Option("min_score", OptionKind.COUNT, flag="--min-score", low=1, high=5),
    },
    CommandName.SCAN: {"since": _SCAN_SINCE},
    # `track` takes the workflow's own `dry_run`, which is also what keeps the state unpushed;
    # the workflow turns it into the `--dry-run` the CLI wants.
    CommandName.TRACK: {"dry": _DRY, "dry_run": _DRY},
    CommandName.COLLECT_BATCHES: {"dry": _DRY, "dry_run": _DRY},
    CommandName.REPREFILTER: {
        "limit": _LIMIT,
        "text_skipped": _INCLUDE_TEXT_SKIPPED,
        "include_text_skipped": _INCLUDE_TEXT_SKIPPED,
    },
    CommandName.INDEX_RCL_NUMBERS: {"since": _SCAN_SINCE},
    CommandName.ANALYZE: {
        "force": Option("force"),
        "publish": Option("publish"),
        "json": Option("json"),
    },
    CommandName.PREVIEW: {"to": Option("to", OptionKind.CHAT)},
    CommandName.RESET: {"to": Option("to", OptionKind.STATUS)},
    CommandName.RUNS: {"days": _DAYS},
    CommandName.COST: {"days": _DAYS, "top": Option("top", OptionKind.COUNT, high=50)},
    CommandName.DIGEST: {
        "ref": Option("ref", OptionKind.WEEK),
        "week": Option("ref", OptionKind.WEEK),
        "publish": Option("publish"),
    },
}

REQUIRED: dict[CommandName, str] = {CommandName.INDEX_RCL_NUMBERS: "since"}


class Command(BaseModel):
    """A parsed command line; `error` says what is wrong with it (the reply repeats it).

    `options` holds what the operator wrote beside the bill, under the canonical name of each
    option and already checked; `query` is what `/find` searches for — the one command that
    names words instead of a bill.
    """

    model_config = ConfigDict(frozen=True)

    name: CommandName
    ref: BillRef | None = None
    query: str | None = None
    options: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    @property
    def force(self) -> bool:
        """`/analyze BILL force`: past the prefilter, a previous analysis and the cost guard."""
        return "force" in self.options

    @property
    def publish(self) -> bool:
        """`/analyze BILL publish`: post a relevant card even below the score threshold."""
        return "publish" in self.options

    @property
    def as_json(self) -> bool:
        return "json" in self.options

    def count(self, option: str, default: int) -> int:
        """A checked `key=N` option, or `default` when the operator did not name it."""
        value = self.options.get(option)
        return default if value is None else int(value)

    @property
    def inputs(self) -> dict[str, str]:
        """The `daily.yml` inputs for a command the relay starts instead of filing.

        Each option is either an input of its own or a flag of the CLI command the workflow runs;
        `command` is left out of an ordinary `/run`, which is the workflow's default.
        """
        spec = {option.name: option for option in OPTIONS.get(self.name, {}).values()}
        inputs: dict[str, str] = {}
        flags: list[str] = []
        for name, value in self.options.items():
            option = spec[name]
            if option.input is not None:
                inputs[option.input] = value
            elif option.flag is not None:
                flags.append(
                    option.flag if option.kind is OptionKind.FLAG else f"{option.flag} {value}"
                )
        if self.name is not CommandName.RUN:
            inputs["command"] = self.name.value
        if flags:
            inputs["options"] = " ".join(flags)
        return inputs


class OutcomeStatus(StrEnum):
    """How a command ended.

    `ANALYSED` carries a verdict, fresh or stored, and a `message_id` when the card went out;
    `SKIPPED` means the prefilter said no and the `note` says why (`force` gets past it);
    `SILENCED` is what `/skip` leaves behind — the bill will not be analysed or posted, and
    `QUEUED` what `/unskip` puts it back into; `PREVIEWED` carries a card that was rendered for
    the technical channel and posted nowhere else; `FORGOTTEN` means the channel no longer
    remembers a card for the bill, and nothing was posted in its place; `EXECUTED_EARLIER`
    means a previous run did the work and only its answer never arrived.
    """

    ANALYSED = "analysed"
    SKIPPED = "skipped"
    SHOWN = "shown"
    SILENCED = "silenced"
    QUEUED = "queued"
    RESET = "reset"
    REPUBLISHED = "republished"
    FORGOTTEN = "forgotten"
    PREVIEWED = "preview"
    REFRESHED = "refreshed"
    FOUND = "found"
    REPORTED = "status"
    LISTED = "runs"
    SPENT = "cost"
    DIGESTED = "digest"
    HELP = "help"
    EXECUTED_EARLIER = "executed earlier"
    NOT_FOUND = "not_found"
    ERROR = "error"


class StatusSnapshot(BaseModel):
    """What `/status` answers: the queues as they stand, what is stuck, and what the last runs
    did and cost.

    `waiting` names the bills rather than counting them: an operator can act on a number and
    not on a total. The run report tells what one run did; this tells what has piled up over
    the days between them.
    """

    model_config = ConfigDict(frozen=True)

    bills: dict[str, int] = Field(default_factory=dict)
    publications: dict[str, int] = Field(default_factory=dict)
    followed: int = 0
    waiting: tuple[Bill, ...] = ()
    stuck: tuple[Publication, ...] = ()
    runs: tuple[RunReport, ...] = ()
    days: int = 0


class SpendSnapshot(BaseModel):
    """What `/cost` answers: the window's model spend per model, its dearest run and bills.

    `runs` counts the reports the window holds, because a total says nothing without them; the
    priciest analyses are the bills as `most_expensive_analyses` ranks them.
    """

    model_config = ConfigDict(frozen=True)

    days: int = 0
    runs: int = 0
    usage: dict[str, TokenUsage] = Field(default_factory=dict)
    dearest: RunReport | None = None
    priciest: tuple[Bill, ...] = ()


class CommandOutcome(BaseModel):
    """What happened to a command; the replier renders it under the command's message.

    `note` is the reason, the error, or why the card was not posted, and `message_id` the card
    just posted (or posted again). `print_info` and `joint_primary` belong to a `/preview`: the
    message is rendered with the links the channel would get, and as the reply it would be when
    the bill joins the thread of the print named there. `found` are the matches of a `/find` and
    `snapshot` the answer to a `/status`. The rest is what running the command took: when the
    run that answered it started, how long the command itself took, and the model tokens it
    spent (empty when the model was not called).
    """

    model_config = ConfigDict(frozen=True)

    status: OutcomeStatus
    bill: Bill | None = None
    note: str = ""
    message_id: int | None = None
    print_info: PrintInfo | None = None
    joint_primary: Bill | None = None
    found: tuple[Bill, ...] = ()
    snapshot: StatusSnapshot | None = None
    runs: tuple[RunReport, ...] = ()
    spend: SpendSnapshot | None = None
    as_json: bool = False
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
# Where `/preview BILL to=…` may send the card: a channel or group id, or a public @username.
_CHAT = re.compile(r"^(-?\d{5,}|@[A-Za-z0-9_]{4,})$")


def parse_reference(text: str) -> BillRef | None:
    """A bill number in any of the bot's notations, or a link to the bill; None otherwise.

    `UD408` and `WPL/UD408` name the same thing: the wykaz number is what RCL, the ministries and
    the register itself use, and the prefix belongs to our row alone.
    """
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
    bare = value[len(WYKAZ_PREFIX) :] if value.upper().startswith(WYKAZ_PREFIX) else value
    if _WYKAZ.match(bare):
        return BillRef(kind=RefKind.WYKAZ, value=normalize_wykaz_number(bare) or bare)
    if _RM.match(value):
        return BillRef(kind=RefKind.RM, value=value.upper())
    return None


Query = dict[str, list[str]]


def _parse_url(value: str) -> BillRef | None:
    """The bill a link points at; each host writes the reference its own way."""
    url = urlparse(value if "://" in value else f"https://{value}")
    host = (url.hostname or "").lower()
    query = parse_qs(url.query)
    if host.endswith("legislacja.rcl.gov.pl"):
        return _rcl_url_ref(url.path, query)
    if host == "api.sejm.gov.pl":
        return _api_url_ref(url.path)
    if host.endswith("sejm.gov.pl"):
        return _sejm_url_ref(url.path, query)
    return None


def _rcl_url_ref(path: str, query: Query) -> BillRef | None:
    if match := _RCL_PATH.search(path):
        return BillRef(kind=RefKind.RCL, value=RCL_PREFIX + match.group(1))
    for rm in query.get("number", []):
        if _RM.match(rm):
            return BillRef(kind=RefKind.RM, value=rm.upper())
    return None


def _api_url_ref(path: str) -> BillRef | None:
    match = _API_PATH.match(path)
    if match is None:
        return None
    return BillRef(kind=RefKind.DRUK, value=str(int(match.group(2))), term=int(match.group(1)))


def _sejm_url_ref(path: str, query: Query) -> BillRef | None:
    term_match = _SEJM_TERM_PATH.search(path)
    term = int(term_match.group(1)) if term_match else None
    for rpw in query.get("NrProjektu", []):
        if _RPW.match(rpw):
            return BillRef(kind=RefKind.RPW, value=rpw.upper(), term=term)
    for nr in query.get("nr", []):
        if nr.isdigit():
            return BillRef(kind=RefKind.DRUK, value=str(int(nr)), term=term)
    return None


def _is_date(value: str) -> bool:
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _checked(name: CommandName, key: str, option: Option, raw: str) -> tuple[str | None, str]:
    """The option's value in the form the workflow or the run wants, or the error to answer with.

    Every value is checked here rather than by the workflow, which would answer a typo hours
    later with a job that did the wrong thing — or nothing, `since` being free text to it.
    """
    if option.kind is OptionKind.FLAG:
        return "true", ""
    if not raw:
        return None, f"/{name}: {key} needs a value ({key}=…)"
    if option.kind is OptionKind.DATE:
        return (raw, "") if _is_date(raw) else (None, f"/{name}: {key} takes a date, not {raw!r}")
    if option.kind is OptionKind.COUNT:
        if not raw.isdigit():
            return None, f"/{name}: {key} takes a number, not {raw!r}"
        if not option.low <= int(raw) <= option.high:
            return None, f"/{name}: {key} takes {option.low}–{option.high}, not {raw}"
        return raw, ""
    if option.kind is OptionKind.CHAT:
        if _CHAT.match(raw):
            return raw, ""
        return None, f"/{name}: {key} takes a chat id (-100…) or @name, not {raw!r}"
    if option.kind is OptionKind.WEEK:
        try:
            week_bounds(raw.upper())
        except ValueError:
            return None, f"/{name}: {key} takes an ISO week (2026-W38), not {raw!r}"
        return raw.upper(), ""
    try:
        return BillStatus(raw.lower()).value, ""
    except ValueError:
        known = ", ".join(status.value for status in BillStatus)
        return None, f"/{name}: {key} takes a status ({known}), not {raw!r}"


def _parse_options(name: CommandName, words: list[str]) -> tuple[dict[str, str], list[str], str]:
    """The command's own options, what is left for the bill or the query, and the first error.

    A word is an option when its key is one this command knows; everything else is passed on
    untouched, so a reference carrying an `=` (a Sejm link) is never read as one.
    """
    spec = OPTIONS.get(name, {})
    options: dict[str, str] = {}
    rest: list[str] = []
    for word in words:
        key, _, raw = word.partition("=")
        option = spec.get(key.lower().lstrip("-").replace("-", "_"))
        if option is None:
            rest.append(word)
            continue
        value, error = _checked(name, key, option, raw)
        if value is None:
            return options, rest, error
        options[option.name] = value
    return options, rest, ""


def parse_command(text: str) -> Command | None:
    """The command in a channel post; None when the post is not a command (no leading slash).

    An unknown command, a missing or unreadable reference come back as `help` with `error`. In a
    group Telegram writes the bot's name into the command itself (`/analyze@lexinform_bot`).
    """
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    head = words[0][1:].split("@", 1)[0].lower()
    name = _COMMAND_NAMES.get(head.replace("_", "-"))
    if name is None:
        return Command(name=CommandName.HELP, error=f"unknown command /{head}")
    options, rest, error = _parse_options(name, words[1:])
    if error:
        return Command(name=name, error=error)
    if (needed := REQUIRED.get(name)) is not None and needed not in options:
        return Command(name=name, error=f"/{name} needs {needed}=… ({needed} is not optional)")
    if name in NEEDS_QUERY:
        query = " ".join(rest).strip()
        if len(query) < MIN_QUERY_CHARS:
            return Command(
                name=name, error=f"/{name} needs at least {MIN_QUERY_CHARS} characters to look for"
            )
        return Command(name=name, query=query, options=options)
    if name in NEEDS_REFERENCE:
        if not rest:
            return Command(name=name, error=f"/{name} needs a bill number or a link")
        ref = parse_reference(" ".join(rest))
        if ref is None:
            # An option of another command is named rather than dropped in silence, which is how
            # an operator comes to believe that `/show BILL force` did something.
            if len(rest) > 1 and parse_reference(rest[0]) is not None:
                return _unknown_option(name, rest[1])
            return Command(
                name=name, error=f"cannot read a bill number or a link in {' '.join(rest)!r}"
            )
        return Command(name=name, ref=ref, options=options)
    if rest and name is not CommandName.HELP:
        return _unknown_option(name, rest[0])
    return Command(name=name, options=options)


def _unknown_option(name: CommandName, word: str) -> Command:
    """What a command does not understand, with what it does: the spellings it accepts, once
    each and in the order they are declared."""
    known = ", ".join(dict.fromkeys(OPTIONS.get(name, {})))
    return Command(
        name=name,
        error=f"/{name}: unknown option {word} — it takes {known or 'none'}",
    )
