"""An RCL project as a bill: its summary, consultation window and "what comes next"."""

import datetime as dt

from lexinform.models import (
    AnalysisRecord,
    ApplicantType,
    Bill,
    BillStatus,
    RclConsultation,
    RclProject,
    has_process,
    is_pre_print_number,
    is_rcl_number,
    next_phase,
    process_summary,
    process_web_url,
    rcl_stages,
)
from tests.fakes import make_analysis
from tests.harness import RCL, RCL_CONSULTATION, RCL_ID, rcl_project, rcl_stage

NOW = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
TODAY = dt.date(2026, 9, 7)


def rcl_bill(project: RclProject, *, analysed: bool = True) -> Bill:
    record = AnalysisRecord(
        analysis=make_analysis(),
        model="m",
        prompt_version="v",
        input_chars=10,
        truncated=False,
        text_source="documents",
        created_at=NOW,
    )
    return Bill(
        summary=process_summary(project, term=10),
        status=BillStatus.ANALYZED,
        stages=rcl_stages(project),
        analysis=record if analysed else None,
        rcl=project,
        first_seen_at=NOW,
        last_checked_at=NOW,
    )


def test_numbering_tells_the_three_kinds_of_bills_apart() -> None:
    assert (is_rcl_number(RCL), is_pre_print_number(RCL), has_process(RCL)) == (True, False, False)
    assert (has_process("RPW/1/2026"), has_process("3039")) == (False, True)
    assert process_web_url(10, RCL) == f"https://legislacja.rcl.gov.pl/projekt/{RCL_ID}"


def test_summary_of_a_project_is_a_government_bill_without_a_process() -> None:
    summary = process_summary(rcl_project(), term=10)

    assert (summary.number, summary.term, summary.applicant_type) == (
        RCL,
        10,
        ApplicantType.GOVERNMENT,
    )
    assert summary.description is not None
    assert summary.description.startswith("CUDZOZIEMCY; SYSTEM INFORMACYJNY SCHENGEN; sprawy")
    assert summary.change_date == dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    assert (summary.process_start_date, summary.document_date) == (
        dt.date(2026, 8, 31),
        dt.date(2026, 9, 1),  # the newest text document
    )
    assert summary.eu_related and summary.rcl_link == summary.web_url
    assert summary.closure_date is None and not summary.has_process


def test_closed_project_gets_a_closure_date_unless_it_went_to_the_sejm() -> None:
    closed = process_summary(rcl_project(status="zamknięty"), term=10)
    sent = process_summary(rcl_project(status="zamknięty", rm_number="RM-1"), term=10)

    assert closed.closure_date == dt.date(2026, 9, 1)
    assert sent.closure_date is None and sent.rcl_num == "RM-1"


def test_consultation_window_comes_from_the_letter() -> None:
    bill = rcl_bill(rcl_project())

    window = bill.consultation

    assert window is not None
    assert (window.source, window.end, window.email) == (
        "rcl",
        dt.date(2026, 9, 8),
        "dep.prawny@mswia.gov.pl",
    )
    assert window.letter_url is not None and window.form_url is None
    assert window.is_open(dt.date(2026, 9, 8)) and not window.is_open(dt.date(2026, 9, 9))
    assert rcl_bill(rcl_project(consultation=None)).consultation is None


def test_next_phase_follows_the_government_path() -> None:
    consulting = rcl_bill(rcl_project())
    opinions = rcl_bill(rcl_project(consultation=None))
    committees = rcl_bill(
        rcl_project(
            consultation=None,
            stages=(
                rcl_stage(3, "Konsultacje publiczne"),
                rcl_stage(9, "Stały Komitet Rady Ministrów", "active"),
            ),
        )
    )
    council = rcl_bill(
        rcl_project(consultation=None, stages=(rcl_stage(12, "Rada Ministrów", "active"),))
    )
    sent = rcl_bill(rcl_project(rm_number="RM-0610-139-26"))
    closed = rcl_bill(rcl_project(status="zamknięty", consultation=None))
    unknown_deadline = rcl_bill(
        rcl_project(consultation=RclConsultation(email="a@b.pl", letter_url="https://x/pismo.pdf"))
    )

    during = next_phase(consulting, today=TODAY)
    after = next_phase(consulting, today=dt.date(2026, 9, 9))
    keys = [
        phase.key if phase else None
        for phase in (
            next_phase(bill, today=TODAY)
            for bill in (opinions, committees, council, sent, unknown_deadline, closed)
        )
    ]

    assert during is not None
    assert (during.key, during.date) == ("rcl_consultation", dt.date(2026, 9, 8))
    assert after is not None and after.key == "rcl_opinions"
    assert keys == [
        "rcl_opinions",
        "rcl_committees",
        "rcl_council",
        "rcl_to_sejm",
        "rcl_opinions",
        None,
    ]


def test_generic_stages_of_a_project_end_with_the_active_one() -> None:
    bill = rcl_bill(rcl_project())

    assert bill.last_stage is not None
    assert bill.last_stage.stage_name == "4. Opiniowanie"
    assert RCL_CONSULTATION.is_open(TODAY)
