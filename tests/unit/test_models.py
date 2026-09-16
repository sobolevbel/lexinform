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
    veto_stood,
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
    """A bill whose process is still open, whatever the fixture says.

    The ELI goes with the closure date: a fixture taken from a finished process carries the act's
    address, and `next_phase` reads that as the road being over before it looks at a stage.
    """
    return Bill(
        summary=process.model_copy(
            update={"closure_date": None, "passed": None, "eli": None, "display_address": None}
        ),
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


IN_THE_SENATE_WINDOW = dt.date(2026, 7, 20)
"""Three days after druk 1962's third reading: inside the Senate's thirty days, so a walk of the
stage tree sees the Senate step rather than what art. 121 ust. 2 makes of its silence."""


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

    day = IN_THE_SENATE_WINDOW
    phases = [next_phase(_bill(process_1962, stages), today=day) for stages in prefixes]
    # The Sejm appends "Uchwalono" at the third reading and keeps it last, so every prefix from
    # then on really arrives with that node; it must not move the bill one step further.
    with_end = [next_phase(_bill(process_1962, (*stages, end)), today=day) for stages in prefixes]

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

    senate = next_phase(in_senate, today=IN_THE_SENATE_WINDOW)
    senate_urgent = next_phase(urgent, today=dt.date(2026, 7, 20))
    president = next_phase(with_president, today=TODAY)

    assert senate is not None and (senate.key, senate.deadline) == ("senate", dt.date(2026, 8, 16))
    assert senate_urgent is not None and senate_urgent.deadline == dt.date(2026, 7, 31)
    assert president is not None
    assert (president.key, president.deadline) == ("president", dt.date(2026, 9, 28))
    # `ToPresident` is the hand-over itself, so those 21 days are counted from the day they start.
    assert president.deadline_exact and not senate.deadline_exact


def test_a_senate_that_let_its_thirty_days_pass_puts_the_bill_with_the_president(
    process_1962: ProcessDetail,
) -> None:
    """Art. 121 ust. 2: the term is zawity — the Senate can neither extend nor suspend it — so
    silence is an adoption and the step has really moved on. Annotating the Senate step instead
    made one line promise «рассмотрение в Сенате (до 30 дней)» and deny it in the same breath."""
    third = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and "III" in st.stage_name
    )
    in_senate = _bill(process_1962, process_1962.stages[: third + 1])  # passed 2026-07-17

    inside = next_phase(in_senate, today=dt.date(2026, 8, 16))
    in_grace = next_phase(in_senate, today=dt.date(2026, 8, 22))
    after = next_phase(in_senate, today=dt.date(2026, 8, 24))

    assert inside is not None and inside.key == "senate"
    assert in_grace is not None and in_grace.key == "senate"  # DEADLINE_GRACE_DAYS
    assert after is not None and (after.key, after.deadline) == (
        "president_after_senate_silence",
        None,  # the President's 21 days run from a receipt the API does not date
    )


def test_the_budget_and_the_constitution_get_no_senate_deadline_of_ours(
    process_1962: ProcessDetail,
) -> None:
    """Art. 223 gives the Senate twenty days for the budget and art. 235 sixty for an amendment
    to the Constitution. Neither is in reach of this channel's keywords, but a date computed at
    thirty days would be wrong, and the silence rule would then move the bill on too early."""
    third = next(
        i
        for i, st in enumerate(process_1962.stages)
        if st.stage_type == "SejmReading" and "III" in st.stage_name
    )
    stages = process_1962.stages[: third + 1]
    budget = _bill(
        process_1962.model_copy(update={"title": "Ustawa budżetowa na rok 2026"}), stages
    )

    phase = next_phase(budget, today=dt.date(2026, 9, 30))

    assert phase is not None and (phase.key, phase.deadline) == ("senate", None)


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


def test_a_veto_the_sejm_overrode_sends_the_act_back_to_the_president(
    process_1962: ProcessDetail,
) -> None:
    """The Sejm's vote on the President's motion is `PresidentMotionConsideration`, and the
    tree keeps its "Uchwalono" — so without reading the decision the newest stage is unknown
    and the phase fell through to the `SenatePosition` this bill has carried since May."""
    veto = Stage(
        stage_name="Wniosek Prezydenta (weto)", stage_type="Veto", date=dt.date(2026, 8, 28)
    )
    motion = Stage(
        stage_name="Rozpatrywanie na forum Sejmu wniosku Prezydenta",
        stage_type="PresidentMotionConsideration",
        date=dt.date(2026, 9, 4),
        decision="uchwalono ponownie",
    )
    end = process_1962.stages[-1]
    overridden = (*process_1962.stages[:-1], veto, motion, end)
    sustained = motion.model_copy(update={"decision": "nie uchwalona ponownie"})

    phase = next_phase(_bill(process_1962, overridden), today=TODAY)
    killed = next_phase(
        _bill(
            process_1962,
            (
                *overridden[:-2],
                sustained,
                Stage(stage_name="nie uchwalona ponownie po wecie Prezydenta", stage_type="End"),
            ),
        ),
        today=TODAY,
    )

    # Art. 122 ust. 5: seven days to sign, counted from the Sejm's vote, and no way back.
    assert phase is not None and phase.key == "president_after_veto"
    assert phase.deadline == dt.date(2026, 9, 11)
    assert killed is None


def test_a_veto_that_stood_is_read_from_the_vote_not_from_the_end_node(
    process_1962: ProcessDetail,
) -> None:
    """Druki 410, 643, 865, 935, 1109, 1110, 1131 and 1600 of term 10, all closed 2026-03-27:
    the Sejm did not re-adopt them, and the API still leaves `End` = "Uchwalono" and
    `passed` = true. Reading the rename alone left their cards saying nothing about being over.
    """
    veto = Stage(
        stage_name="Wniosek Prezydenta (weto)", stage_type="Veto", date=dt.date(2026, 2, 20)
    )
    motion = Stage(
        stage_name="Rozpatrywanie na forum Sejmu wniosku Prezydenta",
        stage_type="PresidentMotionConsideration",
        date=dt.date(2026, 3, 27),
        decision="nie uchwalona ponownie",
    )
    stages = (
        *process_1962.stages[:-1],
        veto,
        motion,
        Stage(stage_name="Uchwalono", stage_type="End"),
    )
    bill = _bill(process_1962, stages).model_copy(
        update={"summary": process_1962.model_copy(update={"eli": None, "display_address": None})}
    )

    assert veto_stood(stages)
    assert next_phase(bill, today=TODAY) is None
    assert is_over(bill, today=TODAY)


def test_the_senate_rejects_an_act_in_the_words_the_api_uses(
    process_1962: ProcessDetail,
) -> None:
    """The API's only wording is "wnosi o odrzucenie ustawy" (93 positions of terms 8-10);
    "odrzucił ustawę" never occurs, and neither contains the "odrzuci" that used to be tested."""
    position = next(
        i for i, st in enumerate(process_1962.stages) if st.stage_type == "SenatePosition"
    )
    stages = list(process_1962.stages[: position + 1])
    stages[-1] = stages[-1].model_copy(update={"position": "wnosi o odrzucenie ustawy"})

    phase = next_phase(_bill(process_1962, tuple(stages)), today=TODAY)

    assert phase is not None and phase.key == "senate_rejection"


def test_the_sejm_accepting_the_senates_rejection_ends_the_road(
    process_1962: ProcessDetail,
) -> None:
    """Druk 2898 of term 9: "przyjęto uchwałę Senatu" over a position moving rejection, and the
    `End` reads "odrzucono na wniosek Senatu". The Sejm's override says "odrzucono uchwałę
    Senatu" instead, and that one does go on to the President."""
    position = next(
        i for i, st in enumerate(process_1962.stages) if st.stage_type == "SenatePosition"
    )
    stages = list(process_1962.stages[: position + 1])
    stages[-1] = stages[-1].model_copy(update={"position": "wnosi o odrzucenie ustawy"})
    considered = Stage(
        stage_name="Rozpatrywanie na forum Sejmu stanowiska Senatu",
        stage_type="SenatePositionConsideration",
        date=dt.date(2026, 6, 10),
        decision="przyjęto uchwałę Senatu",
    )
    overridden = considered.model_copy(update={"decision": "odrzucono uchwałę Senatu"})

    died = next_phase(_bill(process_1962, (*stages, considered)), today=TODAY)
    stands = next_phase(_bill(process_1962, (*stages, overridden)), today=TODAY)

    assert died is None
    assert stands is not None and stands.key == "president"


def test_a_committee_report_moving_rejection_is_not_a_new_bill_text() -> None:
    """ "odrzucić projekt ustawy" (23 bill reports of term 10) and "uchwalić projekt ustawy bez
    poprawek" (95) both name a projekt and neither attaches one; sending their PDF to the model
    replaced the card's verdict with a reading of the committee's recommendation."""
    attached = _report("2857", proposal="załączony projekt ustawy")
    rejected = _report("1413", proposal="odrzucić projekt ustawy")
    unchanged = _report("197", proposal="uchwalić projekt ustawy bez poprawek")
    amendments = _report("2857-A", proposal="przyjąć część poprawek")

    assert attached.carries_bill_text
    assert not rejected.carries_bill_text
    assert not unchanged.carries_bill_text
    assert not amendments.carries_bill_text
    assert latest_text_document((rejected, unchanged)) is None
    assert latest_text_document((attached,)) is not None


def test_the_report_after_a_first_reading_goes_to_the_second_whatever_it_proposes(
    process_1962: ProcessDetail,
) -> None:
    """What follows the committee is read from the report's print number, not from its proposal:
    every one of the 292 "Praca w komisjach po II czytaniu" stages of term 10 carries an "-A"
    report and none of the 645 after a first reading does."""
    after_first = (
        _stage("Start", "2026-01-01"),
        Stage(
            stage_name="Praca w komisjach po I czytaniu",
            stage_type="CommitteeWork",
            date=dt.date(2026, 2, 1),
            children=(_report("1413", proposal="odrzucić projekt ustawy"),),
        ),
    )
    after_second = (
        after_first[0],
        Stage(
            stage_name="Praca w komisjach po II czytaniu",
            stage_type="CommitteeWork",
            date=dt.date(2026, 3, 1),
            children=(_report("2857-A", proposal="przyjąć część poprawek"),),
        ),
    )

    first = next_phase(_bill(process_1962, after_first), today=TODAY)
    second = next_phase(_bill(process_1962, after_second), today=TODAY)

    assert first is not None and first.key == "second_reading"
    assert second is not None and second.key == "third_reading"


def test_an_adjourned_reading_decided_nothing(process_1962: ProcessDetail) -> None:
    """Druk 2985 of term 8 stood at "nie dokończone III czytanie" with the process open: reading
    that as a decision made `is_over` true, so the bill got no card and a followed one froze.
    Term 10 writes the same thing without the space, and the editors have used both."""
    third = Stage(
        stage_name="III czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        date=dt.date(2026, 8, 20),
        decision="nie dokończone III czytanie",
    )
    bill = _bill(process_1962, (*process_1962.stages[:-1], third))

    phase = next_phase(bill, today=TODAY)

    assert phase is not None and phase.key == "third_reading"
    assert not is_over(bill, today=TODAY)


def test_a_tribunal_ruling_keeps_the_road_open_until_its_consequence_appears(
    process_1962: ProcessDetail,
) -> None:
    """Ten bills of term 10 stand at `PresidentToTribunal`; the ruling that answers them is a
    top-level stage the code did not know, so the tree came out unrecognised."""
    ruling = Stage(
        stage_name="Wyrok Trybunału Konstytucyjnego",
        stage_type="ConstitutionalTribunalRuling",
        date=dt.date(2026, 8, 20),
    )
    bill = _bill(process_1962, (*process_1962.stages[:-1], ruling))

    phase = next_phase(bill, today=TODAY)

    assert phase is not None and phase.key == "tribunal_after_ruling"
    assert not is_over(bill, today=TODAY)


def test_the_committee_answering_the_veto_is_not_answering_the_senate(
    process_1962: ProcessDetail,
) -> None:
    """Both arrive as `CommitteeWork`, and only the tree before them tells the two apart: a bill
    that reached a veto has a `SenatePosition` months behind it."""
    veto = Stage(
        stage_name="Wniosek Prezydenta (weto)", stage_type="Veto", date=dt.date(2026, 8, 28)
    )
    work = Stage(
        stage_name="Praca w komisjach nad wnioskiem Prezydenta",
        stage_type="CommitteeWork",
        date=dt.date(2026, 9, 1),
        children=(
            Stage(stage_name="Sprawozdanie komisji", stage_type="CommitteeReport", proposal="x"),
        ),
    )
    stages = (*process_1962.stages[:-1], veto, work, process_1962.stages[-1])

    phase = next_phase(_bill(process_1962, stages), today=TODAY)

    assert phase is not None and phase.key == "veto"


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
