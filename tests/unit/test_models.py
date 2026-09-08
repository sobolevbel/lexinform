import datetime as dt

from lexinform.models import (
    ApplicantType,
    ClubVotes,
    Stage,
    Vote,
    aggregate_clubs,
    applicant_from_title,
    diff_stages,
    flatten_stages,
    latest_text_document,
    stage_fingerprint,
)


def _stage(name: str, date: str | None = None, **kw: object) -> Stage:
    return Stage(
        stage_name=name, stage_type=name, date=dt.date.fromisoformat(date) if date else None, **kw
    )  # type: ignore[arg-type]


def test_fingerprint_is_stable_and_ignores_volatile_fields() -> None:
    a = (_stage("Start", "2026-01-01", report_file="http://a"),)
    b = (_stage("Start", "2026-01-01", report_file="http://b"),)
    assert stage_fingerprint(a) == stage_fingerprint(b)


def test_fingerprint_changes_when_stage_added_or_dated() -> None:
    base = (_stage("Start", "2026-01-01"),)
    with_end = base + (_stage("End"),)
    with_end_dated = base + (_stage("End", "2026-02-01"),)
    assert (
        len(
            {
                stage_fingerprint(base),
                stage_fingerprint(with_end),
                stage_fingerprint(with_end_dated),
            }
        )
        == 3
    )


def test_diff_reports_new_and_newly_dated_stages_in_order() -> None:
    old = (_stage("Start", "2026-01-01"), _stage("End"))
    new = (
        _stage("Start", "2026-01-01"),
        _stage("Reading", "2026-01-15"),
        _stage("End", "2026-02-01"),
    )
    names = [(s.stage_name, s.date) for s in diff_stages(old, new)]
    assert names == [("Reading", dt.date(2026, 1, 15)), ("End", dt.date(2026, 2, 1))]


def test_flatten_includes_children_after_parent() -> None:
    tree = (
        Stage(stage_name="P", stage_type="P", children=(_stage("C1"), _stage("C2"))),
        _stage("Q"),
    )
    assert [s.stage_name for s in flatten_stages(tree)] == ["P", "C1", "C2", "Q"]


def test_applicant_from_title() -> None:
    assert applicant_from_title("Rządowy projekt ustawy o X") is ApplicantType.GOVERNMENT
    assert applicant_from_title("Poselski projekt ustawy") is ApplicantType.DEPUTIES
    assert (
        applicant_from_title("Przedstawiony przez Prezydenta Rzeczypospolitej Polskiej projekt")
        is ApplicantType.PRESIDENT
    )
    assert applicant_from_title("Obywatelski projekt ustawy") is ApplicantType.CITIZENS
    assert applicant_from_title("Senacki projekt") is ApplicantType.SENATE
    assert applicant_from_title("Komisyjny projekt") is ApplicantType.COMMITTEE
    assert applicant_from_title("Projekt ustawy") is ApplicantType.UNKNOWN


def test_fixture_detail_parses(process_1962) -> None:  # type: ignore[no-untyped-def]
    assert process_1962.passed is True
    assert process_1962.last_stage is not None
    assert process_1962.last_stage.stage_name == "Uchwalono"
    assert len(flatten_stages(process_1962.stages)) == 18


def _report(print_number: str, **kw: object) -> Stage:
    return Stage(
        stage_name="Sprawozdanie komisji",
        stage_type="CommitteeReport",
        print_number=print_number,
        report_file=f"https://api.test/prints/{print_number}/{print_number}.pdf",
        **kw,  # type: ignore[arg-type]
    )


def test_amendment_only_reports_do_not_count_as_bill_text() -> None:
    full = _report("2689", proposal="załączony projekt ustawy")
    amendments = _report("2689-A", proposal="przyjąć poprawki")
    sub = _report("2689", proposal="załączony projekt ustawy", sub_committee=True)
    legacy_full = _report("2689")  # stored before `proposal` was parsed
    legacy_additional = _report("2689-A")
    assert full.carries_bill_text and legacy_full.carries_bill_text
    assert not amendments.carries_bill_text
    assert not sub.carries_bill_text
    assert not legacy_additional.carries_bill_text
    doc = latest_text_document((full, amendments))
    assert doc is not None and doc.url.endswith("/2689/2689.pdf")


def test_presidium_applicant_is_recognised() -> None:
    title = "Przedstawiony przez Prezydium Sejmu projekt uchwały w sprawie ..."
    assert applicant_from_title(title) is ApplicantType.PRESIDIUM


def test_fingerprint_ignores_enrichment_fields(process_1962) -> None:  # type: ignore[no-untyped-def]
    def strip(stages):  # type: ignore[no-untyped-def]
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
    voting = next(s for s in flatten_stages(process_1962.stages) if s.stage_type == "Voting")
    assert voting.voting is not None and voting.voting.clubs == ()


def test_aggregate_clubs_counts_and_orders() -> None:
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


# --------------------------------------------------------------------------- next phase


def _bill_with(process, stages, **kw):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from lexinform.models import Bill, BillStatus

    now = datetime(2026, 9, 9, tzinfo=UTC)
    summary = process.model_copy(update={"closure_date": None, "passed": None})
    return Bill(
        summary=summary,
        status=BillStatus.ANALYZED,
        stages=stages,
        first_seen_at=now,
        last_checked_at=now,
        **kw,
    )


def test_next_phase_walks_the_whole_process(process_1962, process_950) -> None:  # type: ignore[no-untyped-def]
    import datetime as dt

    from lexinform.models import next_phase

    today = dt.date(2026, 9, 9)
    expected_1962 = [
        "first_reading",  # Start
        "first_reading_committee",  # ReadingReferral -> SPC
        "committee_work",  # Reading: I czytanie w komisjach
        "second_reading",  # CommitteeWork with the report carrying the text
        "third_reading",  # II czytanie, sent back to the committee
        "third_reading",  # -A report answering amendments
        "senate",  # III czytanie: uchwalono
        "senate_amendments",  # Senate introduced amendments
        "senate_amendments",  # committee work on the Senate position
        "president",  # Sejm considered the Senate position
    ]
    keys = [
        next_phase(_bill_with(process_1962, process_1962.stages[:i]), today=today)
        for i in range(1, len(process_1962.stages))
    ]
    assert [k.key for k in keys if k] == expected_1962
    assert keys[1].committees == ("SPC",)  # type: ignore[union-attr]
    # first reading at a sitting (committeeCode "Sejm") is not a committee referral
    at_sitting = next_phase(_bill_with(process_950, process_950.stages[:2]), today=today)
    assert at_sitting is not None and at_sitting.key == "first_reading_sitting"
    after_first = next_phase(_bill_with(process_950, process_950.stages[:3]), today=today)
    assert after_first is not None and after_first.committees == ("NZC",)
    # "nie wniósł poprawek" goes straight to the President
    senate_ok = process_1962.stages[7].model_copy(update={"position": "nie wniósł poprawek"})
    stages = process_1962.stages[:7] + (senate_ok,)
    assert next_phase(_bill_with(process_1962, stages), today=today).key == "president"  # type: ignore[union-attr]
    # the whole process: publication pending, then the act
    done = _bill_with(process_1962, process_1962.stages)
    done.summary = done.summary.model_copy(update={"passed": True})
    assert next_phase(done, today=today).key == "publication"  # type: ignore[union-attr]


def test_next_phase_for_acts_pre_prints_and_closed_processes(process_3039) -> None:  # type: ignore[no-untyped-def]
    import datetime as dt
    from datetime import UTC, datetime

    from lexinform.models import ActInfo, BillSubmission, next_phase

    today = dt.date(2026, 9, 9)
    act = ActInfo(
        eli="DU/2026/1",
        display_address="Dz.U. 2026 poz. 1",
        title="t",
        entry_into_force=dt.date(2026, 11, 19),
        fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    bill = _bill_with(process_3039, process_3039.stages, act=act)
    phase = next_phase(bill, today=today)
    assert phase is not None and phase.key == "in_force" and phase.date == dt.date(2026, 11, 19)
    assert next_phase(bill, today=dt.date(2026, 11, 19)) is None
    unknown = bill.model_copy(update={"act": act.model_copy(update={"entry_into_force": None})})
    assert next_phase(unknown, today=today).key == "in_force_unknown"  # type: ignore[union-attr]

    sub = BillSubmission(
        term=10,
        number="RPW/1/2026",
        title="t",
        date_of_receipt=dt.date(2026, 9, 1),
        public_consultation=True,
        consultation_end=dt.date(2026, 9, 30),
    )
    pre = _bill_with(process_3039, (), submission=sub)
    pre.summary = pre.summary.model_copy(update={"number": "RPW/1/2026"})
    open_ = next_phase(pre, today=today)
    assert open_ is not None and open_.key == "pre_print_consultation"
    assert open_.date == dt.date(2026, 9, 30)
    assert next_phase(pre, today=dt.date(2026, 10, 1)).key == "pre_print"  # type: ignore[union-attr]
    withdrawn = pre.model_copy(
        update={"summary": pre.summary.model_copy(update={"closure_date": dt.date(2026, 9, 5)})}
    )
    assert next_phase(withdrawn, today=today) is None

    rejected = _bill_with(process_3039, process_3039.stages)
    rejected.summary = rejected.summary.model_copy(
        update={"closure_date": dt.date(2026, 9, 5), "passed": False}
    )
    assert next_phase(rejected, today=today) is None


def test_consultation_url_only_for_consulted_submissions() -> None:
    import datetime as dt

    from lexinform.models import BillSubmission

    sub = BillSubmission(
        term=10, number="RPW/29075/2026", title="t", date_of_receipt=dt.date(2026, 8, 31)
    )
    assert sub.consultation_url is None
    consulted = sub.model_copy(update={"public_consultation": True})
    assert consulted.consultation_url == (
        "https://www.sejm.gov.pl/Sejm10.nsf/agent.xsp?symbol=KONSULTOWANY_PROJEKT"
        "&NrProjektu=RPW/29075/2026"
    )
