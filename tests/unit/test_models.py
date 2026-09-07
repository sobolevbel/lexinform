from __future__ import annotations

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
