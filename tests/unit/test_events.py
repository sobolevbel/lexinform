"""Which stage changes are news, and what a status update is named after."""

import datetime as dt
from typing import Any

from lexinform.models import (
    Bill,
    BillStatus,
    ProcessDetail,
    Stage,
    StatusChange,
    flatten_stages,
    has_news,
    hearing_application_deadline,
    is_substantive,
    update_event,
)

NOW = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)


def _bill(process: ProcessDetail, **fields: Any) -> Bill:
    return Bill(
        summary=process,
        status=BillStatus.ANALYZED,
        stages=process.stages,
        first_seen_at=NOW,
        last_checked_at=NOW,
        **fields,
    )


def _change(stages: list[Stage], **fields: Any) -> StatusChange:
    return StatusChange(
        term=10,
        number="1962",
        old_fingerprint="a",
        new_fingerprint="b",
        new_stages=stages,
        detected_at=NOW,
        **fields,
    )


def test_frame_stages_are_service_and_decisions_are_substantive(
    process_1962: ProcessDetail,
) -> None:
    by_type = {st.stage_type: st for st in flatten_stages(process_1962.stages)}
    readings = [st for st in process_1962.stages if st.stage_type == "SejmReading"]
    second, third = readings[0], readings[1]
    plain_second = second.model_copy(update={"decision": None})
    rejected_first = Stage(
        stage_name="I czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        decision="odrzucono",
    )

    assert not is_substantive(by_type["Start"])
    assert not is_substantive(by_type["ReadingReferral"])
    assert not is_substantive(by_type["Reading"])
    assert not is_substantive(by_type["CommitteeWork"])
    assert not is_substantive(by_type["End"])
    assert not is_substantive(plain_second)
    assert is_substantive(second)  # sent back to the committee with amendments
    assert is_substantive(third)
    assert is_substantive(rejected_first)
    for kind in ("Referral", "CommitteeReport", "Voting", "SenatePosition"):
        assert is_substantive(by_type[kind]), kind


def test_has_news_needs_a_substantive_stage_a_text_or_a_closure(
    process_1962: ProcessDetail,
) -> None:
    by_type = {st.stage_type: st for st in flatten_stages(process_1962.stages)}

    assert not has_news(_change([by_type["Start"], by_type["ReadingReferral"]]))
    assert has_news(_change([by_type["ReadingReferral"], by_type["Referral"]]))
    assert has_news(_change([], content_changed=True))
    assert has_news(_change([by_type["End"]], closure_detected=True, passed=True))
    # The closure that arrives with the act in Dziennik Ustaw is told by the publication notice.
    assert not has_news(
        _change([by_type["End"]], closure_detected=True, passed=True), act_published=True
    )
    assert has_news(_change([], withdrawn=True)) and has_news(_change([], discontinued=True))


def test_update_event_is_the_newest_stage_or_the_flag(process_1962: ProcessDetail) -> None:
    bill = _bill(process_1962)
    flat = flatten_stages(process_1962.stages)
    by_type = {st.stage_type: st for st in flat}
    readings = [st for st in process_1962.stages if st.stage_type == "SejmReading"]
    third = readings[1]
    referrals = [st for st in flat if st.stage_type == "Referral"]

    assert update_event(_change([by_type["ReadingReferral"], referrals[0]]), bill) == "referral"
    assert update_event(_change([by_type["ReadingReferral"], *referrals[:2]]), bill) == "referrals"
    assert update_event(_change([readings[0]]), bill) == "second_reading_amendments"
    assert update_event(_change([third, by_type["Voting"]]), bill) == "passed"
    assert update_event(_change([by_type["SenatePosition"]]), bill) == "senate_amendments"
    assert update_event(_change([by_type["SenatePositionConsideration"]]), bill) == (
        "senate_considered"
    )
    assert update_event(_change([by_type["End"]], closure_detected=True, passed=True), bill) == (
        "passed"
    )
    assert update_event(_change([], closure_detected=True, passed=False), bill) == "rejected"
    assert update_event(_change([], content_changed=True), bill) == "text_changed"
    assert update_event(_change([], discontinued=True), bill) == "discontinued"
    assert update_event(_change([by_type["Start"]]), bill) == "start"
    linked = _bill(process_1962, linked_number="RPW/1/2026")
    assigned = _change([by_type["Start"]]).model_copy(update={"old_fingerprint": "RPW/1/2026"})
    assert update_event(assigned, linked) == "print_assigned"


def test_hearing_application_deadline_is_ten_days_before() -> None:
    dated = Stage(
        stage_name="Wysłuchanie publiczne", stage_type="PublicHearing", date=dt.date(2026, 9, 30)
    )
    undated = dated.model_copy(update={"date": None})

    assert hearing_application_deadline(dated) == dt.date(2026, 9, 20)
    assert hearing_application_deadline(undated) is None
