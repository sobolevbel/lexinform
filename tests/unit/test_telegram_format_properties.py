"""What must hold of every rendered message, whatever the sources put in it.

Telegram rejects a message over 4096 characters or with markup it cannot parse, and a rejected
message is a post the channel never sees. Everything a message is built from — the model's prose,
a Polish title, an operator's command, an exception message — comes from outside this repo, so the
guarantee has to hold for any of it, not only for the values the example tests happen to use.
"""

import datetime as dt
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from lexinform.adapters.sejm_api import parse_process_detail
from lexinform.adapters.telegram_format import MESSAGE_LIMIT, MessageFormatter
from lexinform.models import (
    AgendaItem,
    Analysis,
    Bill,
    Category,
    CommandOutcome,
    IncomingCommand,
    OutcomeStatus,
    RunMode,
    RunReport,
    Stage,
    StatusChange,
)
from tests.conftest import load_json
from tests.harness import act
from tests.unit.test_telegram_format import assert_telegram_html, bill_of, consulted

NOW = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)

# The shapes that break markup or a length budget: entities, tag-like text, combining marks,
# bidirectional overrides, a lone surrogate's escape, and runs long enough to fill a message.
NASTY = st.text(st.sampled_from("<>&\"'\n\t «»—…​‮́абвAZ09\U0001f600ąćńó;#"), max_size=400)
LONG = st.builds(lambda s, n: s * n, NASTY, st.integers(min_value=1, max_value=40))
TEXT = st.one_of(NASTY, LONG)
DAY = st.dates(min_value=dt.date(2024, 1, 1), max_value=dt.date(2030, 1, 1))


@st.composite
def analyses(draw: st.DrawFn) -> Analysis:
    return Analysis(
        relevant=draw(st.booleans()),
        score=draw(st.integers(min_value=1, max_value=5)),
        category=draw(st.sampled_from(list(Category))),
        summary=draw(TEXT),
        key_changes=draw(st.lists(TEXT, max_size=8)),
        affected_groups=draw(st.lists(TEXT, max_size=6)),
        practical_impact=draw(TEXT),
        effective_date=draw(st.one_of(st.none(), TEXT)),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0)),
        rationale=draw(TEXT),
        changes_since_previous=draw(st.lists(TEXT, max_size=5)),
    )


@st.composite
def bills(draw: st.DrawFn, process: Any) -> Bill:
    stages = draw(
        st.lists(
            st.builds(
                Stage,
                stage_name=TEXT,
                stage_type=st.sampled_from(
                    ["Start", "ReadingReferral", "SejmReading", "CommitteeWork", "SenatePosition"]
                ),
                date=st.one_of(st.none(), DAY),
            ),
            max_size=6,
        )
    )
    summary = process.model_copy(update={"title": draw(TEXT)})
    return bill_of(
        summary.model_copy(update={"stages": tuple(stages)}),
        draw(analyses()),
        submission=draw(st.one_of(st.none(), st.just(consulted()))),
        act=draw(st.one_of(st.none(), st.just(act()))),
    )


def _process() -> Any:
    return parse_process_detail(load_json("process_3039.json"))


SLOW = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@SLOW
@given(data=st.data(), today=DAY)
def test_every_card_is_a_message_telegram_accepts(data: st.DataObject, today: dt.date) -> None:
    bill = data.draw(bills(_process()))

    assert_telegram_html(MessageFormatter("ru").new_bill(bill, None, today=today).text)


@SLOW
@given(data=st.data(), today=DAY)
def test_every_reply_under_a_card_is_a_message_telegram_accepts(
    data: st.DataObject, today: dt.date
) -> None:
    bill = data.draw(bills(_process()))
    fmt = MessageFormatter("ru")
    change = StatusChange(
        term=10,
        number=bill.number,
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=list(bill.stages),
        detected_at=NOW,
    )
    hearing = Stage(stage_name="Wysłuchanie publiczne", stage_type="PublicHearing", date=today)
    item = AgendaItem(
        kind="sejm", ref="sejm/1/x", date=today, sitting_number=1, text=data.draw(TEXT)
    )

    assert_telegram_html(fmt.status_update(bill, change, today=today).text)
    assert_telegram_html(fmt.act_published(bill.model_copy(update={"act": act()})).text)
    assert_telegram_html(fmt.in_force(bill.model_copy(update={"act": act()}), today=today).text)
    assert_telegram_html(fmt.agenda(bill, item, today=today).text)
    assert_telegram_html(fmt.hearing_deadline(bill, hearing, today=today).text)
    if bill.consultation is not None and bill.consultation.end is not None:
        assert_telegram_html(fmt.consultation_deadline(bill, today=today).text)
        assert_telegram_html(fmt.consultation_results(bill, today=today).text)


@SLOW
@given(
    errors=st.lists(TEXT, max_size=12),
    notes=st.lists(TEXT, max_size=6),
    commands=st.lists(TEXT, max_size=6),
    logs=st.lists(TEXT, max_size=30),
)
def test_the_run_report_survives_whatever_a_failing_library_wrote(
    errors: list[str], notes: list[str], commands: list[str], logs: list[str]
) -> None:
    report = RunReport(
        started_at=NOW,
        since=NOW,
        mode=RunMode.RUN,
        finished_at=NOW,
        errors=errors,
        notes=notes,
        commands=commands,
    )

    assert_telegram_html(MessageFormatter("ru").run_report(report, logs).text)


@SLOW
@given(text=TEXT, note=TEXT, status=st.sampled_from(list(OutcomeStatus)))
def test_a_command_reply_fits_whatever_the_operator_typed(
    text: str, note: str, status: OutcomeStatus
) -> None:
    command = IncomingCommand(update_id=1, chat_id="-100", message_id=2, text=text, received_at=NOW)
    outcome = CommandOutcome(status=status, note=note)

    assert_telegram_html(MessageFormatter("ru").command_reply(command, outcome).text)


def test_the_limit_is_counted_the_way_telegram_counts_it() -> None:
    """Telegram measures a message in UTF-16 code units, so every emoji counts twice."""
    command = IncomingCommand(
        update_id=1, chat_id="-100", message_id=2, text="🙂" * 3000, received_at=NOW
    )
    outcome = CommandOutcome(status=OutcomeStatus.SKIPPED, note="🙂" * 3000)

    text = MessageFormatter("ru").command_reply(command, outcome).text

    assert len(text.encode("utf-16-le")) // 2 <= MESSAGE_LIMIT
