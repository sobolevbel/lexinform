"""The weekly digest and the monthly figures that open the first digest of a month."""

import datetime as dt
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from lexinform.models.analysis import TokenUsage
from lexinform.models.enums import Category, PublicationKind
from lexinform.models.report import RunReport
from lexinform.models.sejm import AgendaItem

DIGEST_NUMBER = "DIGEST"
"""The sentinel a digest's publication row carries: `term`/`number` are NOT NULL and every other
unique index of the table is keyed on them, so the digest gets its own on `(channel, kind, ref)`."""

WEEK_REF_FORMAT = "%G-W%V"


def iso_week(day: dt.date) -> str:
    """The week `day` falls in, as the digest names it (`2026-W38`)."""
    return day.strftime(WEEK_REF_FORMAT)


def week_bounds(ref: str) -> tuple[dt.date, dt.date]:
    """Monday and Sunday of the ISO week `ref`; `ValueError` when it is not one."""
    year, _, week = ref.partition("-W")
    if not week.isdigit() or not year.isdigit():
        raise ValueError(f"{ref!r} is not an ISO week (2026-W38)")
    monday = dt.date.fromisocalendar(int(year), int(week), 1)
    return monday, monday + dt.timedelta(days=6)


def previous_week(day: dt.date) -> str:
    """The week that has just ended, which is the one a Sunday's digest is about."""
    return iso_week(day - dt.timedelta(days=7))


def is_first_digest_of_month(ref: str) -> bool:
    """Whether the week carries the month's figures: read off its Sunday, so a week that
    straddles the turn belongs to the month it ends in and the figures are complete."""
    _, sunday = week_bounds(ref)
    return sunday.day <= 7


class DigestEntry(BaseModel):
    """One post of the week: which bill, what it said, and where in the channel to read it."""

    model_config = ConfigDict(frozen=True)

    term: int
    number: str
    title: str
    kind: PublicationKind
    sent_at: dt.datetime
    message_id: int | None = None
    score: int | None = None
    category: Category | None = None
    wykaz_number: str | None = None
    event: str = ""
    """What an update said, keyed as `models.update_event` keys it; empty for a card."""


class Upcoming(BaseModel):
    """A window the reader can still act in: a consultation closing, or a sitting ahead."""

    model_config = ConfigDict(frozen=True)

    term: int
    number: str
    title: str
    message_id: int | None = None
    wykaz_number: str | None = None
    deadline: dt.date | None = None
    sitting: AgendaItem | None = None


class MonthFigures(BaseModel):
    """What the month caught and what it cost, from the run records it kept."""

    model_config = ConfigDict(frozen=True)

    month: dt.date
    runs: int = 0
    discovered: int = 0
    keyword_hits: int = 0
    analyzed: int = 0
    triaged_out: int = 0
    published: int = 0
    updates: int = 0
    usage: dict[str, TokenUsage] = Field(default_factory=dict)

    @property
    def dropped_by_keywords(self) -> int:
        """Entries the keywords answered for, on the title or the text: the model never saw them."""
        return max(0, self.discovered - self.keyword_hits)

    @classmethod
    def of(cls, month: dt.date, reports: list[RunReport]) -> Self:
        """The figures of the month `month` falls in, counting rows *taken in* and never the
        `seen` of a phase: the register is downloaded whole every run and the Sejm listing is
        re-read with a day of overlap, so a sum of those counts one entry sixty times."""
        usage: dict[str, TokenUsage] = {}
        for report in reports:
            for model, spent in report.llm_usage.items():
                usage[model] = usage.get(model, TokenUsage()).plus(spent)
        return cls(
            month=month.replace(day=1),
            runs=len(reports),
            discovered=sum(
                r.discovered + r.pre_print_discovered + r.rcl_discovered + r.wykaz_discovered
                for r in reports
            ),
            keyword_hits=sum(
                r.prefilter_hits + r.rcl_prefilter_hits + r.text_prefilter_hits for r in reports
            ),
            analyzed=sum(r.analyzed for r in reports),
            triaged_out=sum(r.triaged_out for r in reports),
            published=sum(r.published + r.joint_published for r in reports),
            updates=sum(r.updates for r in reports),
            usage=usage,
        )


class Digest(BaseModel):
    """A week of the channel; built afresh whenever it is rendered, so a draft left standing
    overnight is rebuilt before a reader ever sees it."""

    model_config = ConfigDict(frozen=True)

    ref: str
    term: int
    since: dt.date
    until: dt.date
    cards: tuple[DigestEntry, ...] = ()
    updates: tuple[DigestEntry, ...] = ()
    consultations: tuple[Upcoming, ...] = ()
    sittings: tuple[Upcoming, ...] = ()
    month: MonthFigures | None = None

    @property
    def is_empty(self) -> bool:
        """Nothing posted and nothing ahead; the digest still goes out, silence being news in a
        channel about deadlines."""
        return not (self.cards or self.updates or self.consultations or self.sittings)
