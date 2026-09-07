from __future__ import annotations

import datetime as dt

from lexinform.models import (
    ApplicantType,
    Stage,
    applicant_from_title,
    diff_stages,
    flatten_stages,
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
