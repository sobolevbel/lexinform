"""Pure helpers over the Sejm models: fingerprints, diffs, text documents, next phase."""

import datetime as dt
from typing import Any

from lexinform.models import (
    ActInfo,
    ApplicantType,
    Bill,
    BillStatus,
    BillSubmission,
    ClubVotes,
    ProcessDetail,
    ProcessSummary,
    SejmTerm,
    Stage,
    Vote,
    aggregate_clubs,
    applicant_from_title,
    current_term,
    diff_stages,
    flatten_stages,
    is_over,
    latest_text_document,
    next_phase,
    stage_fingerprint,
    third_reading_kept_the_text,
    wykaz_summary,
)
from tests.harness import wykaz_entry

NOW = dt.datetime(2026, 9, 9, tzinfo=dt.UTC)
TODAY = NOW.date()


def _stage(name: str, date: str | None = None, **fields: Any) -> Stage:
    return Stage(
        stage_name=name,
        stage_type=name,
        date=dt.date.fromisoformat(date) if date else None,
        **fields,
    )


def _report(print_number: str, **fields: Any) -> Stage:
    return Stage(
        stage_name="Sprawozdanie komisji",
        stage_type="CommitteeReport",
        print_number=print_number,
        report_file=f"https://api.test/prints/{print_number}/{print_number}.pdf",
        **fields,
    )


def _bill(process: ProcessDetail, stages: tuple[Stage, ...], **fields: Any) -> Bill:
    """A bill whose process is still open, whatever the fixture says."""
    return Bill(
        summary=process.model_copy(update={"closure_date": None, "passed": None}),
        status=BillStatus.ANALYZED,
        stages=stages,
        first_seen_at=NOW,
        last_checked_at=NOW,
        **fields,
    )


def test_fingerprint_ignores_volatile_fields() -> None:
    a = (_stage("Start", "2026-01-01", report_file="http://a"),)
    b = (_stage("Start", "2026-01-01", report_file="http://b"),)

    assert stage_fingerprint(a) == stage_fingerprint(b)


def test_fingerprint_changes_when_a_stage_is_added_or_dated() -> None:
    base = (_stage("Start", "2026-01-01"),)
    with_end = base + (_stage("End"),)
    with_end_dated = base + (_stage("End", "2026-02-01"),)

    fingerprints = {stage_fingerprint(s) for s in (base, with_end, with_end_dated)}

    assert len(fingerprints) == 3


def test_fingerprint_ignores_the_enrichment_fields(process_1962: ProcessDetail) -> None:
    def strip(stages: tuple[Stage, ...]) -> tuple[Stage, ...]:
        return tuple(
            st.model_copy(
                update={
                    "voting": None,
                    "position": None,
                    "committee_name": None,
                    "children": strip(st.children),
                }
            )
            for st in stages
        )

    assert stage_fingerprint(process_1962.stages) == stage_fingerprint(strip(process_1962.stages))


def test_diff_reports_new_and_newly_dated_stages_in_order() -> None:
    old = (_stage("Start", "2026-01-01"), _stage("End"))
    new = (
        _stage("Start", "2026-01-01"),
        _stage("Reading", "2026-01-15"),
        _stage("End", "2026-02-01"),
    )

    names = [(s.stage_name, s.date) for s in diff_stages(old, new)]

    assert names == [("Reading", dt.date(2026, 1, 15)), ("End", dt.date(2026, 2, 1))]


def test_flatten_puts_children_after_their_parent() -> None:
    tree = (
        Stage(stage_name="P", stage_type="P", children=(_stage("C1"), _stage("C2"))),
        _stage("Q"),
    )

    assert [s.stage_name for s in flatten_stages(tree)] == ["P", "C1", "C2", "Q"]


def test_fixture_process_parses_into_the_stage_tree(process_1962: ProcessDetail) -> None:
    assert process_1962.passed is True
    assert process_1962.last_stage is not None
    assert process_1962.last_stage.stage_name == "Uchwalono"
    assert len(flatten_stages(process_1962.stages)) == 18
    reports = [
        st for st in flatten_stages(process_1962.stages) if st.stage_type == "CommitteeReport"
    ]
    assert [r.minority_motions for r in reports] == [0, 0, 0]


def test_third_reading_keeps_the_text_only_without_amendments_or_minority_motions(
    process_1962: ProcessDetail,
) -> None:
    # 1962: amendments at the 2nd reading ("-A" report): the text after the 3rd reading differs.
    assert not third_reading_kept_the_text(process_1962.stages)
    report = _report("2689", proposal="załączony projekt ustawy", minority_motions=0)
    work = Stage(stage_name="Praca w komisjach", stage_type="CommitteeWork", children=(report,))
    straight = Stage(
        stage_name="II czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        decision="niezwłocznie przystąpiono do III czytania",
    )
    sent_back = straight.model_copy(update={"decision": "skierowano ponownie do komisji"})
    third = Stage(stage_name="III czytanie", stage_type="SejmReading", decision="uchwalono")

    assert third_reading_kept_the_text([work, straight, third])
    assert not third_reading_kept_the_text([work, sent_back, third])
    assert not third_reading_kept_the_text([work, third])  # no 2nd reading seen: may differ
    with_motions = work.model_copy(
        update={"children": (report.model_copy(update={"minority_motions": 1}),)}
    )
    assert not third_reading_kept_the_text([with_motions, straight, third])
    unknown = work.model_copy(
        update={"children": (report.model_copy(update={"minority_motions": None}),)}
    )
    assert not third_reading_kept_the_text([unknown, straight, third])
    assert latest_text_document([work, straight, third], before_third_reading=True) is not None


def test_applicant_is_read_from_the_title_prefix() -> None:
    cases = {
        "Rządowy projekt ustawy o X": ApplicantType.GOVERNMENT,
        "Poselski projekt ustawy": ApplicantType.DEPUTIES,
        "Przedstawiony przez Prezydenta Rzeczypospolitej Polskiej projekt": ApplicantType.PRESIDENT,
        "Przedstawiony przez Prezydium Sejmu projekt uchwały": ApplicantType.PRESIDIUM,
        "Obywatelski projekt ustawy": ApplicantType.CITIZENS,
        "Senacki projekt": ApplicantType.SENATE,
        "Komisyjny projekt": ApplicantType.COMMITTEE,
        "Projekt ustawy": ApplicantType.UNKNOWN,
    }

    assert {title: applicant_from_title(title) for title in cases} == cases


def test_only_reports_with_the_bill_text_count_as_a_new_text() -> None:
    full = _report("2689", proposal="załączony projekt ustawy")
    amendments = _report("2689-A", proposal="przyjąć poprawki")
    sub = _report("2689", proposal="załączony projekt ustawy", sub_committee=True)
    legacy_full = _report("2689")  # stored before `proposal` was parsed
    legacy_additional = _report("2689-A")

    assert full.carries_bill_text and legacy_full.carries_bill_text
    assert not amendments.carries_bill_text
    assert not sub.carries_bill_text
    assert not legacy_additional.carries_bill_text


def test_latest_text_document_prefers_the_last_full_report() -> None:
    full = _report("2689", proposal="załączony projekt ustawy")
    amendments = _report("2689-A", proposal="przyjąć poprawki")

    doc = latest_text_document((full, amendments))

    assert doc is not None and doc.url.endswith("/2689/2689.pdf")
    assert doc.kind == "committee_report"


def test_aggregate_clubs_counts_and_orders_by_yes_votes() -> None:
    votes = [
        Vote(mp=1, club="PiS", vote="ABSTAIN"),
        Vote(mp=2, club="KO", vote="YES"),
        Vote(mp=3, club="KO", vote="YES"),
        Vote(mp=4, club="KO", vote="ABSENT"),
        Vote(mp=5, club="Lewica", vote="YES"),
        Vote(mp=6, club="", vote="NO"),
    ]

    clubs = aggregate_clubs(votes)

    assert [c.club for c in clubs] == ["KO", "Lewica", "niez.", "PiS"]
    assert clubs[0] == ClubVotes(club="KO", yes=2, absent=1)
    assert clubs[3].abstain == 1


def test_consultation_url_exists_only_for_consulted_submissions() -> None:
    plain = BillSubmission(
        term=10, number="RPW/29075/2026", title="t", date_of_receipt=dt.date(2026, 8, 31)
    )
    consulted = plain.model_copy(update={"public_consultation": True})

    assert plain.consultation_url is None
    assert consulted.consultation_url == (
        "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT"
        "&NrProjektu=RPW/29075/2026"
    )


def test_next_phase_walks_a_bill_amended_by_the_senate(process_1962: ProcessDetail) -> None:
    expected = [
        "first_reading",  # Start
        "first_reading_committee",  # ReadingReferral -> SPC
        "committee_work",  # Reading: I czytanie w komisjach
        "second_reading",  # CommitteeWork with the report carrying the text
        "second_reading_committee",  # II czytanie, sent back to the committee
        "third_reading",  # -A report answering amendments
        "senate",  # III czytanie: uchwalono
        "senate_amendments",  # Senate introduced amendments
        "senate_amendments",  # committee work on the Senate position
        "president",  # Sejm considered the Senate position
    ]

    end = process_1962.stages[-1]
    assert end.stage_type == "End"
    prefixes = [process_1962.stages[:i] for i in range(1, len(process_1962.stages))]

    phases = [next_phase(_bill(process_1962, stages), today=TODAY) for stages in prefixes]
    # The Sejm appends "Uchwalono" at the third reading and keeps it last, so every prefix from
    # then on really arrives with that node; it must not move the bill one step further.
    with_end = [next_phase(_bill(process_1962, (*stages, end)), today=TODAY) for stages in prefixes]

    assert [p.key for p in phases if p] == expected
    assert [p.key for p in with_end if p] == expected
    assert phases[1] is not None and phases[1].committees == ("SPC",)


def test_a_second_reading_that_sent_the_bill_back_names_the_committee(
    process_1962: ProcessDetail,
) -> None:
    second = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and st.stage_name.startswith("II ")
    )

    phase = next_phase(_bill(process_1962, process_1962.stages[: second + 1]), today=TODAY)

    assert phase is not None
    assert phase.key == "second_reading_committee"
    assert phase.committees == ("SPC",)


def test_an_unfinished_second_reading_leaves_the_bill_with_the_committee(
    process_1962: ProcessDetail,
) -> None:
    """druk 1929's decision reads "niedokończone II czytanie", not "skierowano ponownie"."""
    second = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and st.stage_name.startswith("II ")
    )
    stages = list(process_1962.stages[: second + 1])
    stages[-1] = stages[-1].model_copy(update={"decision": "niedokończone II czytanie"})

    phase = next_phase(_bill(process_1962, tuple(stages)), today=TODAY)

    assert phase is not None and phase.key == "second_reading_committee"


def test_a_senate_rejection_is_not_a_senate_amendment(process_1962: ProcessDetail) -> None:
    position = next(
        i for i, st in enumerate(process_1962.stages) if st.stage_type == "SenatePosition"
    )
    stages = list(process_1962.stages[: position + 1])
    stages[-1] = stages[-1].model_copy(update={"position": "odrzucił ustawę"})

    phase = next_phase(_bill(process_1962, tuple(stages)), today=TODAY)

    assert phase is not None and phase.key == "senate_rejection"


def test_the_stage_line_ignores_a_position_that_arrived_beside_the_process() -> None:
    committee = Stage(stage_type="Referral", stage_name="Skierowanie", committee_code="ASW")
    aside = Stage(stage_type="GovermentPosition", stage_name="Wpłynęło stanowisko rządu")
    bill = Bill(
        summary=ProcessSummary(
            term=10, number="1273", title="t", document_type="projekt ustawy", change_date=NOW
        ),
        status=BillStatus.ANALYZED,
        stages=(committee, aside),
        first_seen_at=NOW,
        last_checked_at=NOW,
    )

    assert bill.last_stage == committee


def test_a_plan_the_government_has_adopted_is_past_rcl() -> None:
    entry = wykaz_entry(status="Zrealizowany")
    bill = Bill(
        summary=wykaz_summary(entry, term=10),
        status=BillStatus.ANALYZED,
        wykaz=entry,
        first_seen_at=NOW,
        last_checked_at=NOW,
    )

    phase = next_phase(bill, today=TODAY)

    assert phase is not None and phase.key == "wykaz_adopted"


def test_senate_and_president_phases_carry_their_constitutional_deadline(
    process_1962: ProcessDetail,
) -> None:
    third = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and "III" in st.stage_name
    )
    end = process_1962.stages[-1]
    # As the API shows it: "Uchwalono" is already appended, the Senate has not answered yet.
    in_senate = _bill(process_1962, (*process_1962.stages[: third + 1], end))  # passed 2026-07-17
    urgent = _bill(
        process_1962.model_copy(update={"urgency_status": "URGENT"}),
        (*process_1962.stages[: third + 1], end),
    )
    to_president = Stage(
        stage_name="Ustawę przekazano Prezydentowi do podpisu",
        stage_type="ToPresident",
        date=dt.date(2026, 9, 7),
    )
    with_president = _bill(process_1962, (*process_1962.stages[:-1], to_president))

    senate = next_phase(in_senate, today=TODAY)
    senate_urgent = next_phase(urgent, today=TODAY)
    president = next_phase(with_president, today=TODAY)

    assert senate is not None and (senate.key, senate.deadline) == ("senate", dt.date(2026, 8, 16))
    assert senate_urgent is not None and senate_urgent.deadline == dt.date(2026, 7, 31)
    assert president is not None
    assert (president.key, president.deadline) == ("president", dt.date(2026, 9, 28))


def test_first_reading_at_a_plenary_sitting_is_not_a_committee_referral(
    process_950: ProcessDetail,
) -> None:
    at_sitting = next_phase(_bill(process_950, process_950.stages[:2]), today=TODAY)
    after_first_reading = next_phase(_bill(process_950, process_950.stages[:3]), today=TODAY)

    assert at_sitting is not None and at_sitting.key == "first_reading_sitting"
    assert after_first_reading is not None
    assert (after_first_reading.key, after_first_reading.committees) == ("committee_work", ("NZC",))


def test_senate_without_amendments_sends_the_law_to_the_president(
    process_1962: ProcessDetail,
) -> None:
    senate_ok = process_1962.stages[7].model_copy(update={"position": "nie wniósł poprawek"})
    stages = process_1962.stages[:7] + (senate_ok,)

    phase = next_phase(_bill(process_1962, stages), today=TODAY)

    assert phase is not None and phase.key == "president"


def test_a_veto_is_the_current_step_until_the_sejm_answers_it(
    process_1962: ProcessDetail,
) -> None:
    """ "Uchwalono" sits under a veto too, and a bill the veto killed says so with its own node."""
    veto = Stage(
        stage_name="Wniosek Prezydenta o ponowne rozpatrzenie ustawy",
        stage_type="Veto",
        date=dt.date(2026, 9, 20),
    )
    end, killed = (
        process_1962.stages[-1],
        Stage(stage_name="Ustawa nie uchwalona ponownie po wecie Prezydenta", stage_type="End"),
    )
    vetoed = (*process_1962.stages[:-1], veto, end)

    pending = next_phase(_bill(process_1962, vetoed), today=TODAY)
    over = next_phase(_bill(process_1962, (*vetoed[:-1], killed)), today=TODAY)

    assert pending is not None and pending.key == "veto"
    assert over is None


def test_passed_bill_awaits_publication_then_entry_into_force(
    process_1962: ProcessDetail,
) -> None:
    signed = Stage(
        stage_name="Prezydent podpisał ustawę",
        stage_type="PresidentSignature",
        date=dt.date(2026, 9, 20),
    )
    # "Uchwalono" stays last whatever happens after it, so the signature goes in front of it.
    passed = _bill(process_1962, (*process_1962.stages[:-1], signed, process_1962.stages[-1]))
    passed = passed.model_copy(
        update={"summary": passed.summary.model_copy(update={"passed": True})}
    )
    act = ActInfo(
        eli="DU/2026/1",
        display_address="Dz.U. 2026 poz. 1",
        title="t",
        entry_into_force=dt.date(2026, 11, 19),
        fetched_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    )

    awaiting_publication = next_phase(passed, today=TODAY)
    awaiting_force = next_phase(passed.model_copy(update={"act": act}), today=TODAY)
    undated = next_phase(
        passed.model_copy(update={"act": act.model_copy(update={"entry_into_force": None})}),
        today=TODAY,
    )
    in_force = next_phase(passed.model_copy(update={"act": act}), today=dt.date(2026, 11, 19))

    assert awaiting_publication is not None and awaiting_publication.key == "publication"
    assert awaiting_force is not None
    assert (awaiting_force.key, awaiting_force.date) == ("in_force", dt.date(2026, 11, 19))
    assert undated is not None and undated.key == "in_force_unknown"
    assert in_force is None


def test_a_bill_is_over_only_when_the_stages_leave_nothing_ahead(
    process_1962: ProcessDetail,
) -> None:
    bill = _bill(process_1962, process_1962.stages)
    adopted = bill.model_copy(
        update={
            "summary": bill.summary.model_copy(
                update={"closure_date": dt.date(2026, 9, 4), "passed": True}
            )
        }
    )
    rejected = adopted.model_copy(
        update={"summary": adopted.summary.model_copy(update={"passed": False})}
    )
    published = adopted.model_copy(
        update={"summary": adopted.summary.model_copy(update={"eli": "DU/2026/1"})}
    )

    assert not is_over(adopted, today=TODAY)  # closed by the Sejm, the President has it
    assert not is_over(adopted.model_copy(update={"stages": ()}), today=TODAY)  # unread: unknown
    assert is_over(rejected, today=TODAY)
    assert is_over(published, today=TODAY)


def test_pre_print_bill_waits_for_its_consultation_then_its_print_number(
    process_3039: ProcessDetail,
) -> None:
    submission = BillSubmission(
        term=10,
        number="RPW/1/2026",
        title="t",
        date_of_receipt=dt.date(2026, 9, 1),
        public_consultation=True,
        consultation_end=dt.date(2026, 9, 30),
    )
    pre = _bill(process_3039, (), submission=submission)
    pre = pre.model_copy(
        update={"summary": pre.summary.model_copy(update={"number": "RPW/1/2026"})}
    )

    consulting = next_phase(pre, today=TODAY)
    consulted = next_phase(pre, today=dt.date(2026, 10, 1))

    assert consulting is not None
    assert (consulting.key, consulting.date) == ("pre_print_consultation", dt.date(2026, 9, 30))
    assert consulted is not None and consulted.key == "pre_print"


def test_closed_processes_have_no_next_phase(process_3039: ProcessDetail) -> None:
    withdrawn = _bill(process_3039, ())
    withdrawn = withdrawn.model_copy(
        update={
            "summary": withdrawn.summary.model_copy(update={"closure_date": dt.date(2026, 9, 5)})
        }
    )
    rejected = _bill(process_3039, process_3039.stages)
    rejected = rejected.model_copy(
        update={
            "summary": rejected.summary.model_copy(
                update={"closure_date": dt.date(2026, 9, 5), "passed": False}
            )
        }
    )

    assert next_phase(withdrawn, today=TODAY) is None
    assert next_phase(rejected, today=TODAY) is None


def test_next_phase_is_none_for_a_bill_that_lapsed_with_the_term(
    process_3039: ProcessDetail,
) -> None:
    lapsed = _bill(process_3039, process_3039.stages, discontinued_at=NOW)

    assert next_phase(lapsed, today=TODAY) is None


def test_current_term_is_the_flagged_one_else_the_highest_number() -> None:
    flagged = (SejmTerm(num=9), SejmTerm(num=10, current=True), SejmTerm(num=11))
    unflagged = (SejmTerm(num=9), SejmTerm(num=10))

    assert current_term(flagged) == 10
    assert current_term(unflagged) == 10
    assert current_term(()) is None


def test_a_government_position_does_not_hide_what_comes_next(
    process_3039: ProcessDetail,
) -> None:
    """Druk 1273: the government's position arrived while the bill sat in committee and became
    the last top-level stage, so the card lost its path, next step and action block."""
    referral = Stage(
        stage_name="Skierowano do I czytania w komisjach",
        stage_type="ReadingReferral",
        date=dt.date(2025, 6, 10),
        children=(
            Stage(
                stage_name="Skierowanie",
                stage_type="Referral",
                date=dt.date(2025, 6, 10),
                committee_code="ASW",
            ),
        ),
    )
    aside = Stage(
        stage_name="Wpłynęło stanowisko rządu",
        stage_type="GovermentPosition",
        date=dt.date(2025, 8, 21),
    )

    phase = next_phase(_bill(process_3039, (referral, aside)), today=TODAY)
    alone = next_phase(_bill(process_3039, (aside,)), today=TODAY)

    assert phase is not None
    assert phase.key == "first_reading_committee" and phase.committees == ("ASW",)
    assert alone is not None and alone.key == "first_reading"  # nothing but asides yet


def test_the_opinion_survey_lives_on_its_own_host() -> None:
    """Verified 2026-09-13: the project page carries "Link do ankiety" pointing at
    opiniowanie.sejm.gov.pl, with the RPW number's slashes turned into dashes."""
    sub = BillSubmission(
        term=10,
        number="RPW/29075/2026",
        title="t",
        date_of_receipt=dt.date(2026, 8, 31),
        public_consultation=True,
        consultation_end=dt.date(2026, 9, 30),
    )

    assert sub.survey_url == "https://opiniowanie.sejm.gov.pl/RPW-29075-2026"
    assert sub.consultation_url is not None and "KONSULTOWANY_PROJEKT" in sub.consultation_url
    assert sub.model_copy(update={"public_consultation": False}).survey_url is None
