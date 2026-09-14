"""What the rendering tests share: the Telegram-HTML assertion and the objects a message is
rendered from.

`assert_telegram_html` is the one assertion every rendering test makes, whatever it is about,
so it lives here rather than in whichever test file happened to be written first.
"""

import datetime as dt
from html.parser import HTMLParser
from typing import Any

from lexinform.adapters.llm_prompts import PROMPT_VERSION
from lexinform.adapters.telegram_format import MESSAGE_LIMIT, length
from lexinform.models import (
    ActInfo,
    AgendaItem,
    Analysis,
    AnalysisRecord,
    Bill,
    BillStatus,
    BillSubmission,
    ProcessDetail,
    Stage,
    StatusChange,
)
from tests.fakes import make_analysis

NOW = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)

TODAY = dt.date(2026, 9, 9)

CONSULTATION_PAGE = (
    "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT"
    "&amp;NrProjektu=RPW/29075/2026"
)

SURVEY = "https://opiniowanie.sejm.gov.pl/RPW-29075-2026"

COMMITTEE_PAGE = 'KodKom=ASW"'


class _TagChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.balanced = True
        self.tags: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack.append(tag)
        self.tags.add(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.balanced = False


def assert_telegram_html(text: str) -> None:
    """Balanced tags from Telegram's allowed subset, within the message limit.

    The empty stack and the last bracket are what catch a message cut inside its markup, and
    Telegram answers 400 to both shapes: a tag opened and never closed leaves the stack
    non-empty, while a cut inside `<a href=…` is dropped by the parser without a word, so the
    text itself is asked whether its last `<` was ever closed.
    """
    checker = _TagChecker()
    checker.feed(text)
    checker.close()
    assert checker.balanced, text
    assert checker.stack == [], text
    assert text.rfind("<") <= text.rfind(">"), text
    assert checker.tags <= {"b", "i", "a", "code", "pre"}
    assert length(text) <= MESSAGE_LIMIT


def bill_of(
    process: ProcessDetail,
    analysis: Analysis | None = None,
    *,
    truncated: bool = False,
    **fields: Any,
) -> Bill:
    record = AnalysisRecord(
        analysis=analysis or make_analysis(),
        model="m",
        prompt_version=PROMPT_VERSION,
        input_chars=10,
        truncated=truncated,
        text_source="pdf",
        created_at=NOW,
    )
    return Bill(
        summary=process,
        status=BillStatus.ANALYZED,
        stages=process.stages,
        analysis=record,
        first_seen_at=NOW,
        last_checked_at=NOW,
        **fields,
    )


def change_of(number: str, new_stages: list[Stage], **fields: Any) -> StatusChange:
    return StatusChange(
        term=10,
        number=number,
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=new_stages,
        detected_at=NOW,
        **fields,
    )


def consulted(**overrides: Any) -> BillSubmission:
    fields: dict[str, Any] = dict(
        term=10,
        number="RPW/29075/2026",
        title="t",
        date_of_receipt=dt.date(2026, 8, 31),
        public_consultation=True,
        consultation_start=dt.date(2026, 8, 31),
        consultation_end=dt.date(2026, 9, 30),
    )
    fields.update(overrides)
    return BillSubmission(**fields)


def sitting(**overrides: Any) -> AgendaItem:
    fields: dict[str, Any] = dict(
        kind="committee",
        ref="ASW/136/2026-09-17",
        date=dt.date(2026, 9, 17),
        start_time=dt.time(9, 0),
        committee_code="ASW",
        committee_name="Komisja Administracji i Spraw Wewnętrznych",
        sitting_number=136,
        room="sala nr 412",
        text="Pierwsze czytanie projektu (druk nr 3039) – uzasadnia poseł X.",
        video_url="https://sejm.gov.pl/Sejm10.nsf/transmisje_arch.xsp?unid=1",
    )
    fields.update(overrides)
    return AgendaItem(**fields)


PLENARY = sitting(
    kind="sejm",
    ref="sejm/65/2026-09-15",
    date=dt.date(2026, 9, 15),
    end_date=dt.date(2026, 9, 18),
    start_time=None,
    committee_code=None,
    committee_name=None,
    sitting_number=65,
    room=None,
    video_url=None,
    text="Sprawozdanie Komisji (druki nr 3039 i 3055) - sprawozdawca poseł Y.",
)

ACT = ActInfo(
    eli="DU/2026/1099",
    display_address="Dz.U. 2026 poz. 1099",
    title="Ustawa z dnia 17 lipca 2026 r. o zmianie ustawy",
    promulgation_date=dt.date(2026, 8, 18),
    entry_into_force=dt.date(2026, 11, 19),
    text_pdf_url="https://api.sejm.gov.pl/eli/acts/DU/2026/1099/text.pdf",
    isap_url="https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20260001099",
    fetched_at=dt.datetime(2026, 8, 20, tzinfo=dt.UTC),
)
