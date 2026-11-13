"""Operator commands from the technical channel: what the relay files into the inbox and how
the run reads a command line such as `/analyze 3039 force`.

A command names a bill by any number the bot knows (`3039`, `RPW/29075/2026`, `RCL/12414100`,
`UC164`, `RM-0610-139-26`) or by a link to the bill on sejm.gov.pl, api.sejm.gov.pl or
legislacja.rcl.gov.pl. Nothing here does I/O.
"""

import datetime as dt
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self
from urllib.parse import parse_qs, urlparse

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lexinform.json_storage import read_json
from lexinform.models.analysis import AnalysisRecord, TokenUsage
from lexinform.models.batch import BatchKind, LlmBatch
from lexinform.models.bill import Bill, Publication
from lexinform.models.content import ContentHistoryPage, ContentHistoryView
from lexinform.models.digest import week_bounds
from lexinform.models.enums import (
    PRE_PRINT_PREFIX,
    RCL_PREFIX,
    WYKAZ_PREFIX,
    BillStatus,
    PublicationKind,
    PublicationStatus,
)
from lexinform.models.grouped_stage_date import (
    GroupedStageDateApproval,
    GroupedStageDateCommand,
    GroupedStageDateCorrection,
    GroupedStageResolutionCommand,
)
from lexinform.models.rcl import normalize_wykaz_number
from lexinform.models.report import RunReport
from lexinform.models.sejm import PrintInfo, ProcessSummary
from lexinform.models.source_checks import AUTO_OBSERVATION, ObservationMode, SourceCheck
from lexinform.saved_clock import ClockUnknown


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
    actor_id: int | None = None
    author_signature: str | None = None
    acknowledgement_id: int | None = None
    run_requested: bool = False


class CommandWork(BaseModel):
    kind: Literal["analysis", "explanation", "amendments", "delivery", "refresh"]
    term: int
    number: str
    generation: int | None = None
    batch_key: str | None = None
    memo_key: str | None = None
    summary: ProcessSummary | None = None
    publication_id: int | None = None
    publish_below_threshold: bool = False
    publish_allowed: bool = True


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
    incoming: IncomingCommand | None = None
    workflow_url: str | None = None
    work: CommandWork | None = None
    completed_work: CommandWork | None = None
    rendered_reply: str | None = None
    failed: bool = False
    final_answer_pending: bool = False


class DeliveryResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    publication_id: int
    publication_ids: tuple[int, ...] = ()
    term: int
    number: str
    kind: PublicationKind
    channel_id: str
    action: Literal["confirm", "retry", "dismiss"]
    command: IncomingCommand
    resolved_at: dt.datetime
    message_id: int | None = None
    reason: str = ""

    @property
    def status(self) -> PublicationStatus:
        return {
            "confirm": PublicationStatus.SENT,
            "retry": PublicationStatus.QUEUED,
            "dismiss": PublicationStatus.DISMISSED,
        }[self.action]


class GroupedEditDeliveryResolution(DeliveryResolution):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["confirm", "dismiss"]
    resolved_at: AwareDatetime
    content_id: str = Field(min_length=1, strict=True)
    base_content_id: str = Field(min_length=1, strict=True)
    rendered_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    telegram_chat_id: int = Field(gt=-(2**52), lt=0, strict=True)
    target_message_id: int = Field(gt=0, strict=True)
    observed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def verify_attestation(self) -> Self:
        received = self.command.received_at
        if (
            self.publication_ids != (self.publication_id,)
            or self.kind is not PublicationKind.MESSAGE_PART
            or not self.reason.strip()
            or len(self.reason) > 500
            or received.utcoffset() is None
            or received > self.resolved_at
        ):
            raise ValueError("invalid grouped edit attestation")
        if self.action == "confirm":
            if (
                self.message_id != self.target_message_id
                or self.observed_at is None
                or self.observed_at > received
            ):
                raise ValueError("invalid grouped edit observation")
        elif self.message_id is not None or self.observed_at is not None:
            raise ValueError("invalid grouped edit dismissal")
        return self


class GroupedEditResolutionEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["grouped-edit-resolution-v1"]
    resolution: GroupedEditDeliveryResolution


def read_delivery_resolution(raw: str) -> DeliveryResolution:
    value = json.loads(raw)
    if isinstance(value, dict) and ("profile" in value or "resolution" in value):
        return GroupedEditResolutionEnvelope.model_validate(read_json(raw)).resolution
    return DeliveryResolution.model_validate_json(raw)


def card_edit_owner_id(root: Publication) -> int | None:
    plan = root.delivery
    if (
        root.kind is not PublicationKind.MESSAGE_PART
        or plan is None
        or plan.continuation_root is not None
        or plan.edit_message_id is None
    ):
        return None
    if plan.revision_of is not None:
        return plan.revision_of
    pieces = (root.ref or "").split("/")
    if len(pieces) != 4 or pieces[0] != "card" or pieces[-1] != "1":
        return None
    try:
        return int(pieces[1])
    except ValueError:
        return None


def failed_card_part_resolution_allowed(
    publication: Publication,
    root: Publication | None,
    owner: Publication | None,
    action: str,
) -> bool:
    plan = publication.delivery
    if (
        action not in ("retry", "dismiss")
        or publication.kind is not PublicationKind.MESSAGE_PART
        or publication.status is not PublicationStatus.FAILED
        or publication.id is None
        or plan is None
        or not plan.rendered_text
        or plan.publication_ids
        or root is None
        or root.id is None
        or root.delivery is None
        or not root.delivery.rendered_text
        or root.delivery.publication_ids
        or (publication.term, publication.number, publication.channel_id)
        != (root.term, root.number, root.channel_id)
    ):
        return False
    if root.kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL):
        members = root.delivery.continuation_ids
        if publication.id not in members:
            return False
        index = members.index(publication.id)
        return (
            root.status is PublicationStatus.SENT
            and root.message_id is not None
            and root.delivery.edit_message_id is None
            and root.delivery.continuation_root is None
            and root.delivery.revision_of is None
            and plan.edit_message_id is None
            and plan.continuation_root == root.id
            and plan.continuation_parent == (root.id if index == 0 else members[index - 1])
            and plan.continuation_index == index + 2
        )
    if (
        owner is None
        or owner.id is None
        or owner.id != card_edit_owner_id(root)
        or owner.kind is not PublicationKind.NEW_BILL
        or owner.status is not PublicationStatus.SENT
        or owner.message_id is None
        or root.delivery.edit_message_id != owner.message_id
        or (root.term, root.number, root.channel_id) != (owner.term, owner.number, owner.channel_id)
    ):
        return False
    if publication.id == root.id:
        return plan.continuation_root is None
    return (
        root.status is PublicationStatus.SENT
        and plan.continuation_root == root.id
        and publication.id in root.delivery.continuation_ids
    )


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
    actor_id: int | None = None
    author_signature: str | None = None

    def is_from(self, channel: str) -> bool:
        """Whether the post comes from `channel`: a numeric id (`-100…`) or `@username`."""
        wanted = channel.strip()
        if wanted.startswith("@"):
            return (
                self.chat_username is not None and self.chat_username.lower() == wanted[1:].lower()
            )
        return str(self.chat_id) == wanted

    def as_command(self) -> IncomingCommand:
        assert self.text is not None, "the relay files only a post whose text parsed as a command"
        return IncomingCommand(
            update_id=self.update_id,
            chat_id=str(self.chat_id),
            message_id=self.message_id,
            text=self.text,
            received_at=self.date,
            actor_id=self.actor_id,
            author_signature=self.author_signature,
        )


CALLBACKS: dict[str, str] = {
    "digest": "/digest publish ref={}",
    "show": "/show {}",
    "preview": "/preview {}",
    "explain": "/explain {}",
    "delivery": "/delivery {}",
}
"""What a pressed button stands for, by the prefix of its callback data: a button is a command
the operator did not have to type, and it takes the same road as one that was."""


def command_for_callback(data: str) -> str | None:
    """`digest:2026-W38` -> `/digest publish ref=2026-W38`; None for data no button of ours sent."""
    prefix, _, argument = data.partition(":")
    template = CALLBACKS.get(prefix)
    if template is None or not argument:
        return None
    if prefix == "delivery" and not _positive_id(argument):
        return None
    if prefix in ("show", "preview", "explain") and parse_reference(argument) is None:
        return None
    if prefix == "digest" and not re.fullmatch(r"[0-9]{4}-W[0-9]{2}", argument):
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

    Four are missing on purpose: `listen` is the relay reading this channel, `commands` is the
    phase that answers what is written in it, `db` is the workflow's handling of the state branch
    around every run, and `poll-batches` is the mikrus timer that asks for `collect-batches`
    sooner than the schedule — none of them is a thing to ask a run for. The three batch-intent
    repairs are missing too: each needs the provider checked by hand first.
    """

    RUN = "run"
    SCAN = "scan"
    TRACK = "track"
    COLLECT_BATCHES = "collect-batches"
    DRAIN_DELIVERIES = "drain-deliveries"
    REPREFILTER = "reprefilter"
    INDEX_RCL_NUMBERS = "index-rcl-numbers"
    ANALYZE = "analyze"
    EXPLAIN = "explain"
    SHOW = "show"
    SKIP = "skip"
    UNSKIP = "unskip"
    RESET = "reset"
    OBSERVE = "observe"
    REPUBLISH = "republish"
    FORGET = "forget"
    LINK = "link"
    PREVIEW = "preview"
    REFRESH = "refresh"
    AMENDMENTS = "amendments"
    FIND = "find"
    RUNS = "runs"
    COST = "cost"
    STATUS = "status"
    GROUPEDIT = "groupedit"
    DELIVERY = "delivery"
    NAME = "name"
    ATTENTION = "attention"
    DIGEST = "digest"
    HELP = "help"
    QUEUE = "queue"
    REQUEST = "request"


MIN_QUERY_CHARS = 3

_COMMAND_NAMES: dict[str, CommandName] = {name.value: name for name in CommandName} | {
    "index": CommandName.INDEX_RCL_NUMBERS
}


class OptionKind(StrEnum):
    """A bare word (`force`), or `key=value` checked as a date, count, chat, status, ISO week,
    observation mode or druk number."""

    FLAG = "flag"
    DATE = "date"
    COUNT = "count"
    CHAT = "chat"
    STATUS = "status"
    WEEK = "week"
    OBSERVATION = "observation"
    PRINT = "print"


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
_PAGE = Option("page", OptionKind.COUNT, low=1, high=10000)

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
    CommandName.DRAIN_DELIVERIES: {"dry": _DRY, "dry_run": _DRY},
    CommandName.REPREFILTER: {
        "limit": _LIMIT,
        "text_skipped": _INCLUDE_TEXT_SKIPPED,
        "include_text_skipped": _INCLUDE_TEXT_SKIPPED,
    },
    CommandName.INDEX_RCL_NUMBERS: {"since": _SCAN_SINCE},
    CommandName.ANALYZE: {
        "force": Option("force"),
        "recheck": Option("recheck"),
        "reanalyze": Option("reanalyze"),
        "allow_over_budget": Option("allow_over_budget"),
        "publish": Option("publish"),
        "json": Option("json"),
    },
    CommandName.PREVIEW: {"to": Option("to", OptionKind.CHAT)},
    CommandName.AMENDMENTS: {
        "publication": Option("publication", OptionKind.COUNT, low=1),
        "force": Option("force"),
    },
    CommandName.RESET: {"to": Option("to", OptionKind.STATUS)},
    CommandName.OBSERVE: {"mode": Option("mode", OptionKind.OBSERVATION)},
    CommandName.LINK: {"to": Option("to", OptionKind.PRINT)},
    CommandName.RUNS: {"days": _DAYS, "all": Option("all"), "page": _PAGE},
    CommandName.FIND: {"page": _PAGE},
    CommandName.QUEUE: {"page": _PAGE},
    CommandName.COST: {"days": _DAYS, "top": Option("top", OptionKind.COUNT, high=50)},
    CommandName.DIGEST: {
        "ref": Option("ref", OptionKind.WEEK),
        "week": Option("ref", OptionKind.WEEK),
        "publish": Option("publish"),
    },
}


@dataclass(frozen=True)
class CommandSpec:
    usage: str
    description: str
    arguments: Literal["none", "bill", "query", "request", "editorial", "delivery", "help"] = "none"
    lookup: Literal["record", "current", "load", "source"] = "record"
    phase: bool = False
    common: bool = False
    required: str | None = None


COMMANDS: dict[CommandName, CommandSpec] = {
    CommandName.RUN: CommandSpec(
        "[since=DATE] [dry] [reprefilter=N] [text_skipped] [index_rcl_since=DATE] "
        "[no_publish] [no_track] [no_rcl] [full_track] "
        "[max_publish=N] [max_analyze=N] [min_score=N]",
        "Полный прогон. Возможны расходы и публикации. "
        "dry сохраняет только журнал запроса и вывод прогона.",
        phase=True,
        common=True,
    ),
    CommandName.SCAN: CommandSpec(
        "[since=DATE]",
        "Обнаружить проекты и проверить ключевые слова. Без модели и публикаций.",
        phase=True,
    ),
    CommandName.TRACK: CommandSpec(
        "[dry]",
        "Проверить все отслеживаемые проекты. Возможны расходы и обновления читателям.",
        phase=True,
    ),
    CommandName.COLLECT_BATCHES: CommandSpec(
        "[dry]", "Собрать batch и применить результаты, включая публикации.", phase=True
    ),
    CommandName.DRAIN_DELIVERIES: CommandSpec(
        "[dry]",
        "Отправить готовые доставки. Без чтения источников и новых запросов к модели.",
        phase=True,
    ),
    CommandName.REPREFILTER: CommandSpec(
        "[limit=N] [text_skipped]",
        "Повторить проверку текстов. Прошедшие фильтр проекты могут попасть в анализ позже.",
        phase=True,
    ),
    CommandName.INDEX_RCL_NUMBERS: CommandSpec(
        "since=DATE",
        "Обновить индекс номеров проектов RCL. Алиас /index.",
        phase=True,
        required="since",
    ),
    CommandName.ANALYZE: CommandSpec(
        "BILL [recheck] [reanalyze] [allow_over_budget] [publish] [json]",
        "Проверить и проанализировать проект. Карточка может уйти читателям. "
        "recheck обходит фильтр, reanalyze повторяет анализ, allow_over_budget снимает лимит "
        "проекта. Старый force сочетает все три действия. "
        "publish разрешает карточку релевантного проекта ниже порога.",
        "bill",
        "load",
        common=True,
    ),
    CommandName.EXPLAIN: CommandSpec(
        "BILL",
        "Запросить полный разбор через платный batch. Ответ придёт в технический канал.",
        "bill",
        "source",
        common=True,
    ),
    CommandName.SHOW: CommandSpec(
        "BILL", "Показать сохранённые сведения о проекте.", "bill", "current", common=True
    ),
    CommandName.SKIP: CommandSpec(
        "BILL",
        "Исключить из автоматического анализа и первой публикации. "
        "Существующая карточка остаётся в сопровождении.",
        "bill",
    ),
    CommandName.UNSKIP: CommandSpec(
        "BILL", "Вернуть в работу. Если анализ сохранён, использовать его.", "bill"
    ),
    CommandName.RESET: CommandSpec(
        "BILL [to=analysis_pending|skipped_prefilter|skipped_cost|analyzed]",
        "Сбросить попытки анализа или перевести проект в допустимый пропуск. "
        "analyzed требует сохранённого анализа. "
        "Для восстановления готового анализа используйте /unskip.",
        "bill",
    ),
    CommandName.OBSERVE: CommandSpec(
        "BILL mode=full|metadata|off|auto",
        "Настроить наблюдение за проектом без карточки. "
        "Карточка в канале всегда сопровождается полностью.",
        "bill",
        required="mode",
    ),
    CommandName.REPUBLISH: CommandSpec(
        "BILL", "Опубликовать карточку заново. Старая карточка в Telegram остаётся.", "bill"
    ),
    CommandName.FORGET: CommandSpec(
        "BILL",
        "Отвязать вручную удалённую карточку. Следующий прогон может опубликовать её заново.",
        "bill",
    ),
    CommandName.GROUPEDIT: CommandSpec(
        "preview|approve|resolve …",
        "Проверить или применить исправление даты в сводном сообщении. "
        "Для подтверждения используйте команду из предпросмотра.",
    ),
    CommandName.LINK: CommandSpec(
        "ENTRY to=DRUK",
        "Связать запись RCL или RPW с druk и продолжить тему.",
        "bill",
        required="to",
    ),
    CommandName.PREVIEW: CommandSpec(
        "BILL [to=CHAT]",
        "Показать карточку в техническом или тестовом канале. Без анализа.",
        "bill",
        "current",
        common=True,
    ),
    CommandName.REFRESH: CommandSpec(
        "BILL",
        "Проверить один проект. Возможны расходы и обновления читателям.",
        "bill",
        "current",
        common=True,
    ),
    CommandName.AMENDMENTS: CommandSpec(
        "BILL [publication=ID] [force]",
        "Заказать платный разбор поправок и обновить существующее сообщение после batch.",
        "bill",
        "current",
    ),
    CommandName.FIND: CommandSpec(
        "WORDS [page=N]", "Искать сохранённые проекты по названию или номеру.", "query", common=True
    ),
    CommandName.RUNS: CommandSpec(
        "[days=N] [all] [page=N]", "Показать историю прогонов по страницам."
    ),
    CommandName.COST: CommandSpec("[days=N] [top=N]", "Показать расходы на модели."),
    CommandName.STATUS: CommandSpec(
        "", "Показать состояние бота, batch и застрявшие доставки.", common=True
    ),
    CommandName.DELIVERY: CommandSpec(
        "ID [history [PAGE] | confirm MESSAGE_ID | retry | dismiss REASON]",
        "Проверить сохранённую доставку. "
        "confirm, retry и dismiss меняют состояние после сверки с Telegram.",
        "delivery",
        common=True,
    ),
    CommandName.NAME: CommandSpec(
        "BILL [NAME | REASON]",
        "Показать или изменить редакторское название проекта.",
        "editorial",
        "current",
    ),
    CommandName.ATTENTION: CommandSpec(
        "BILL [important|urgent until=DATE | REASON]",
        "Показать историю событий или повысить приоритет до указанной даты.",
        "editorial",
        "current",
    ),
    CommandName.DIGEST: CommandSpec(
        "[ref=2026-W38] [publish]",
        "Подготовить черновик дайджеста. publish отправляет его читателям.",
        common=True,
    ),
    CommandName.HELP: CommandSpec(
        "[COMMAND|all]", "Показать краткую или адресную справку.", "help"
    ),
    CommandName.QUEUE: CommandSpec(
        "[page=N]", "Показать ожидающие запросы оператора.", common=True
    ),
    CommandName.REQUEST: CommandSpec(
        "ID", "Показать этап выполнения и итог запроса по его номеру.", "request", common=True
    ),
}

NEEDS_REFERENCE = frozenset(name for name, spec in COMMANDS.items() if spec.arguments == "bill")
DISPATCHED = frozenset(name for name, spec in COMMANDS.items() if spec.phase)


def command_help(name: CommandName | None = None, *, all_commands: bool = False) -> str:
    if name is not None:
        spec = COMMANDS[name]
        return f"/{name.value} {spec.usage}".rstrip() + "\n" + spec.description
    lines = [
        f"/{name.value} · {spec.description.split('. ', 1)[0].rstrip('.')}"
        for name, spec in COMMANDS.items()
        if all_commands or spec.common
    ]
    return "\n".join((*lines, "", "Подробности: /help COMMAND. Все команды: /help all."))


OBSERVATION_CHOICES = (*(mode.value for mode in ObservationMode), AUTO_OBSERVATION)


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
    request_id: int | None = None
    help_topic: CommandName | None = None
    help_all: bool = False
    options: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    grouped_stage_date: GroupedStageDateCommand | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    grouped_stage_resolution: GroupedStageResolutionCommand | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    delivery_id: int | None = None
    delivery_action: Literal["show", "history", "confirm", "retry", "dismiss"] = "show"
    delivery_history_page: int = Field(default=1, exclude_if=lambda value: value == 1)
    delivery_message_id: int | None = None
    delivery_reason: str = ""
    editorial_name: str = ""
    editorial_reason: str = ""
    editorial_priority: Literal["important", "urgent"] | None = None
    editorial_until: dt.date | None = None

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
    OBSERVED = "observed"
    REPUBLISHED = "republished"
    FORGOTTEN = "forgotten"
    LINKED = "linked"
    PREVIEWED = "preview"
    REFRESHED = "refreshed"
    FOUND = "found"
    REPORTED = "status"
    LISTED = "runs"
    SPENT = "cost"
    DIGESTED = "digest"
    DELIVERY = "delivery"
    HELP = "help"
    EXECUTED_EARLIER = "executed earlier"
    NOT_FOUND = "not_found"
    ERROR = "error"


class IntentAgeGroup(BaseModel):
    """Transient submission-intent row counts and age since recorded creation."""

    model_config = ConfigDict(frozen=True)

    purpose: BatchKind
    state: Literal["queued", "submitting"]
    count: int
    known_ages: int
    unknown_ages: dict[ClockUnknown, int] = Field(default_factory=dict)
    oldest_seconds: int | None = None
    oldest_term: int | None = None
    oldest_number: str | None = None


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
    batches: tuple[LlmBatch, ...] = ()
    queued_intents: int = 0
    uncertain_intents: int = 0
    intent_ages: tuple[IntentAgeGroup, ...] = Field(default=(), exclude_if=lambda value: not value)
    document_work: dict[str, int] = Field(default_factory=dict)
    # `batch_pending` with neither an open batch item nor an intent: nothing will ever answer it.
    orphaned: tuple[Bill, ...] = ()
    awaiting_batch: tuple[Bill, ...] = ()
    # A source that keeps not answering for one bill: no run report counts what it was not given.
    failing: tuple[tuple[Bill, SourceCheck], ...] = ()
    observed_at: dt.datetime | None = None


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
    the bill joins the thread of the print named there. `/show` uses `joint_primary` for the
    current stage and actions of a published joint reply. `found` are the matches of a `/find`
    and `snapshot` the answer to a `/status`. The rest is what running the command took: when the
    run that answered it started, how long the command itself took, and the model tokens it
    spent (empty when the model was not called).
    """

    model_config = ConfigDict(frozen=True)

    status: OutcomeStatus
    work: CommandWork | None = None
    rendered_reply: str | None = None
    publication_id: int | None = None
    request_links: tuple[str, ...] = ()
    bill: Bill | None = None
    note: str = ""
    message_id: int | None = None
    print_info: PrintInfo | None = None
    joint_primary: Bill | None = None
    found: tuple[Bill, ...] = ()
    snapshot: StatusSnapshot | None = None
    runs: tuple[RunReport, ...] = ()
    spend: SpendSnapshot | None = None
    delivery: Publication | None = None
    digest_owner: Publication | None = None
    digest_members: tuple[int, ...] = ()
    delivery_history: tuple[DeliveryResolution, ...] = ()
    content_history: ContentHistoryView | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    content_history_page: ContentHistoryPage | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    as_json: bool = False
    explanation: AnalysisRecord | None = None
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


_DRUK = re.compile(r"^(?:druk\s*(?:nr\s*)?)?(\d{1,5})(?::(\d{1,4}))?$", re.IGNORECASE)
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
        return BillRef(
            kind=RefKind.DRUK,
            value=str(int(match.group(1))),
            term=int(match.group(2)) if match.group(2) is not None else None,
        )
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
    try:
        url = urlparse(value if "://" in value else f"https://{value}")
    except ValueError:
        return None
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
        return (
            (dt.date.fromisoformat(raw).isoformat(), "")
            if _is_date(raw)
            else (None, f"/{name}: {key} takes a date, not {raw!r}")
        )
    if option.kind is OptionKind.COUNT:
        if not raw.isascii() or not raw.isdigit() or len(raw) > 10:
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
    if option.kind is OptionKind.PRINT:
        if raw.isascii() and raw.isdigit() and len(raw) <= 5 and int(raw) > 0:
            return str(int(raw)), ""
        return None, f"/{name}: {key} takes a druk number (3281), not {raw!r}"
    if option.kind is OptionKind.OBSERVATION:
        if raw.lower() in OBSERVATION_CHOICES:
            return raw.lower(), ""
        return None, f"/{name}: {key} takes {', '.join(OBSERVATION_CHOICES)}, not {raw!r}"
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
        key, separator, raw = word.partition("=")
        option = spec.get(key.lower().lstrip("-").replace("-", "_"))
        if option is None:
            rest.append(word)
            continue
        if option.kind is OptionKind.FLAG and separator:
            return options, rest, f"/{name}: {key} — флаг без значения; укажите его или уберите"
        value, error = _checked(name, key, option, raw)
        if value is None:
            return options, rest, error
        if option.name in options and options[option.name] != value:
            return options, rest, f"/{name}: для {option.name} указаны разные значения"
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
    spec = COMMANDS[name]
    if spec.arguments == "help":
        if len(words) == 1:
            return Command(name=name)
        if len(words) == 2:
            topic = words[1].lower().lstrip("/").replace("_", "-")
            if topic == "all":
                return Command(name=name, help_all=True)
            if target := _COMMAND_NAMES.get(topic):
                return Command(name=name, help_topic=target)
        return Command(name=name, error="Укажите /help COMMAND или /help all")
    if spec.arguments == "request":
        if len(words) == 2 and _positive_id(words[1]):
            return Command(name=name, request_id=int(words[1]))
        return Command(name=name, error="Укажите номер запроса: /request ID")
    if spec.arguments == "delivery":
        return _parse_delivery(words[1:])
    if name is CommandName.GROUPEDIT:
        return _parse_groupedit(words[1:])
    if spec.arguments == "editorial":
        return _parse_editorial(name, words[1:])
    options, rest, error = _parse_options(name, words[1:])
    if error:
        return Command(name=name, error=error)
    if (needed := spec.required) is not None and needed not in options:
        return Command(name=name, error=f"/{name} needs {needed}=… ({needed} is not optional)")
    if spec.arguments == "query":
        query = " ".join(rest).strip()
        if len(query) < MIN_QUERY_CHARS:
            return Command(
                name=name, error=f"/{name} needs at least {MIN_QUERY_CHARS} characters to look for"
            )
        return Command(name=name, query=query, options=options)
    if spec.arguments == "bill":
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


def _parse_groupedit(words: list[str]) -> Command:
    if words and words[0] in ("confirm", "dismiss"):
        return _parse_groupedit_resolution(words)
    usage = (
        "use /groupedit preview ROOT MEMBER EVENT CONTENT_ID BASE_SHA OLD_DATE NEW_DATE"
        " or /groupedit approve ROOT MEMBER EVENT CONTENT_ID BASE_SHA OLD_DATE NEW_DATE"
        " CANDIDATE_SHA CHAT_ID MESSAGE_ID"
    )
    invalid = Command(name=CommandName.GROUPEDIT, error=usage)
    if not words or (words[0], len(words)) not in (("preview", 8), ("approve", 11)):
        return invalid
    _, root, member, event, content, baseline, old, new, *approval = words
    if any(
        re.fullmatch(r"[1-9][0-9]{0,18}", value) is None for value in (root, member, event)
    ) or any(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None for value in (old, new)):
        return invalid
    try:
        correction = GroupedStageDateCorrection(
            root_publication_id=int(root),
            member_publication_id=int(member),
            status_change_id=int(event),
            expected_content_id=content,
            expected_sha256=baseline,
            expected_date=dt.date.fromisoformat(old),
            corrected_date=dt.date.fromisoformat(new),
        )
        accepted = None
        if approval:
            candidate, chat, message = approval
            if (
                re.fullmatch(r"-[1-9][0-9]{0,15}", chat) is None
                or re.fullmatch(r"[1-9][0-9]{0,18}", message) is None
            ):
                return invalid
            accepted = GroupedStageDateApproval(
                candidate_sha256=candidate, chat_id=int(chat), message_id=int(message)
            )
    except ValueError:
        return invalid
    return Command(
        name=CommandName.GROUPEDIT,
        grouped_stage_date=GroupedStageDateCommand(correction=correction, approval=accepted),
    )


def _parse_groupedit_resolution(words: list[str]) -> Command:
    invalid = Command(
        name=CommandName.GROUPEDIT,
        error=(
            "use /groupedit confirm SLOT CONTENT_ID BASE_ID HTML_SHA CHAT_ID MESSAGE_ID"
            " OBSERVED_ISO8601 | REASON or /groupedit dismiss SLOT CONTENT_ID BASE_ID"
            " HTML_SHA CHAT_ID MESSAGE_ID | REASON"
        ),
    )
    action: Literal["confirm", "dismiss"] = "confirm" if words[0] == "confirm" else "dismiss"
    delimiter = 8 if action == "confirm" else 7
    if len(words) <= delimiter + 1 or words[delimiter] != "|":
        return invalid
    _, slot, content, base, sha, chat, message = words[:7]
    if (
        any(re.fullmatch(r"[1-9][0-9]{0,18}", value) is None for value in (slot, message))
        or re.fullmatch(r"-[1-9][0-9]{0,15}", chat) is None
    ):
        return invalid
    try:
        observed = None
        if action == "confirm":
            raw = words[7]
            if re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-5][0-9])",
                raw,
            ) is None or raw.endswith("-00:00"):
                return invalid
            observed = dt.datetime.fromisoformat(raw)
        resolution = GroupedStageResolutionCommand(
            action=action,
            slot_id=int(slot),
            content_id=content,
            base_content_id=base,
            rendered_sha256=sha,
            chat_id=int(chat),
            message_id=int(message),
            observed_at=observed,
            reason=" ".join(words[delimiter + 1 :]),
        )
    except ValueError:
        return invalid
    return Command(name=CommandName.GROUPEDIT, grouped_stage_resolution=resolution)


def _parse_editorial(name: CommandName, words: list[str]) -> Command:
    if not words or (ref := parse_reference(words[0])) is None:
        return Command(name=name, error=f"/{name} needs a bill number or link")
    if len(words) == 1:
        return Command(name=name, ref=ref)
    value, separator, reason = " ".join(words[1:]).partition("|")
    if not separator or not reason.strip():
        return Command(name=name, error=f"/{name} requires a reason after |")
    if name is CommandName.NAME:
        return Command(
            name=name, ref=ref, editorial_name=value.strip(), editorial_reason=reason.strip()
        )
    priority, _, until = value.strip().partition(" until=")
    try:
        day = dt.date.fromisoformat(until)
    except ValueError:
        return Command(
            name=name, error="use /attention BILL important|urgent until=YYYY-MM-DD | REASON"
        )
    if priority not in ("important", "urgent"):
        return Command(
            name=name,
            error="priority must be important or urgent; protected facts cannot be downgraded",
        )
    return Command(
        name=name,
        ref=ref,
        editorial_priority=priority,
        editorial_until=day,
        editorial_reason=reason.strip(),
    )


def _positive_id(raw: str) -> bool:
    return raw.isascii() and raw.isdigit() and len(raw) <= 19 and 0 < int(raw) <= 2**63 - 1


def _parse_delivery(words: list[str]) -> Command:
    error = Command(
        name=CommandName.DELIVERY,
        error="use /delivery ID [history [PAGE] | confirm MESSAGE_ID | retry | dismiss REASON]",
    )
    if not words or not _positive_id(words[0]):
        return error
    publication_id = int(words[0])
    if len(words) == 1:
        return Command(name=CommandName.DELIVERY, delivery_id=publication_id)
    action = words[1].lower()
    if action == "history" and len(words) in (2, 3):
        page = words[2] if len(words) == 3 else "1"
        if page.isascii() and page.isdigit() and len(page) <= 10 and 0 < int(page) <= 2_147_483_647:
            return Command(
                name=CommandName.DELIVERY,
                delivery_id=publication_id,
                delivery_action="history",
                delivery_history_page=int(page),
            )
        return error
    if action == "retry" and len(words) == 2:
        return Command(
            name=CommandName.DELIVERY, delivery_id=publication_id, delivery_action="retry"
        )
    if action == "dismiss" and len(words) >= 3:
        return Command(
            name=CommandName.DELIVERY,
            delivery_id=publication_id,
            delivery_action="dismiss",
            delivery_reason=" ".join(words[2:]),
        )
    if (
        action == "confirm"
        and len(words) == 3
        and _positive_id(words[2])
        and 0 < int(words[2]) <= 2_147_483_647
    ):
        return Command(
            name=CommandName.DELIVERY,
            delivery_id=publication_id,
            delivery_action="confirm",
            delivery_message_id=int(words[2]),
        )
    return error


def _unknown_option(name: CommandName, word: str) -> Command:
    """What a command does not understand, with what it does: the spellings it accepts, once
    each and in the order they are declared."""
    known = ", ".join(dict.fromkeys(OPTIONS.get(name, {})))
    return Command(
        name=name,
        error=f"/{name}: unknown option {word} — it takes {known or 'none'}",
    )
