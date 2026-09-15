"""Which stage changes are news, and what a status update is named after."""

import datetime as dt
from typing import Any

from lexinform.models import (
    Bill,
    BillStatus,
    BillSubmission,
    ProcessDetail,
    SourceKind,
    Stage,
    StatusChange,
    SupplementRecord,
    amendments_stage,
    event_keys,
    fills_in_the_past,
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


def _filed(kind: SourceKind) -> SupplementRecord:
    return SupplementRecord(
        number="1962-s", title="Do druku nr 1962", source_kind=kind, source_url=""
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
    # Nothing in the stages says the Sejm rejected it: the ending is told without a culprit.
    assert update_event(_change([], closure_detected=True, passed=False), bill) == "not_enacted"
    assert update_event(_change([], content_changed=True), bill) == "text_changed"
    assert update_event(_change([], discontinued=True), bill) == "discontinued"
    assert update_event(_change([by_type["Start"]]), bill) == "start"
    linked = _bill(process_1962, linked_number="RPW/1/2026")
    assigned = _change([by_type["Start"]]).model_copy(update={"old_fingerprint": "RPW/1/2026"})
    assert update_event(assigned, linked) == "print_assigned"


def test_amendments_stage_is_the_senate_print_or_a_report_on_amendments(
    process_1962: ProcessDetail,
) -> None:
    flat = flatten_stages(process_1962.stages)
    senate = next(st for st in flat if st.stage_type == "SenatePosition")
    reports = [st for st in flat if st.stage_type == "CommitteeReport"]
    full_text, a_report, on_senate = reports  # załączony projekt / przyjąć poprawki / część
    no_amendments = senate.model_copy(update={"position": "nie wniósł poprawek"})

    assert amendments_stage([senate]) is senate
    assert amendments_stage([full_text]) is None  # a new bill text: re-analysed instead
    assert amendments_stage([a_report]) is a_report
    assert amendments_stage([senate, on_senate]) is on_senate  # the newest wins
    assert amendments_stage([no_amendments]) is None
    assert amendments_stage([]) is None


def test_hearing_application_deadline_is_ten_days_before() -> None:
    dated = Stage(
        stage_name="Wysłuchanie publiczne", stage_type="PublicHearing", date=dt.date(2026, 9, 30)
    )
    undated = dated.model_copy(update={"date": None})

    assert hearing_application_deadline(dated) == dt.date(2026, 9, 20)
    assert hearing_application_deadline(undated) is None


def test_a_veto_the_sejm_could_not_override_is_not_a_rejection(
    process_1962: ProcessDetail,
) -> None:
    """The re-vote fails and the process closes with `passed=false`, which the listing cannot
    tell from a rejection at first reading."""
    veto = Stage(stage_type="Veto", stage_name="Wniosek Prezydenta (weto)")
    end = Stage(stage_type="End", stage_name="nie uchwalona ponownie po wecie Prezydenta")
    vetoed = _bill(process_1962).model_copy(update={"stages": (veto, end)})

    change = _change([end], closure_detected=True, passed=False)

    event = update_event(change, vetoed)

    assert event == "veto_sustained"
    # The closing post's only new stage is the `End` node, so without naming the event a search
    # for the veto would miss the post that says how it finished.
    assert "veto" in event_keys(change, event)


def test_a_veto_that_stood_is_not_a_law_the_sejm_passed(process_1962: ProcessDetail) -> None:
    """Druk 410 of term 10 and seven like it: the Sejm did not re-adopt them, and the listing
    still says `passed`. Believing it headed the post that closes the road «Сейм принял закон»."""
    veto = Stage(stage_type="Veto", stage_name="Wniosek Prezydenta (weto)")
    motion = Stage(
        stage_type="PresidentMotionConsideration",
        stage_name="Rozpatrywanie na forum Sejmu wniosku Prezydenta",
        date=dt.date(2026, 3, 27),
        decision="nie uchwalona ponownie",
    )
    end = Stage(stage_type="End", stage_name="Uchwalono")
    vetoed = _bill(process_1962).model_copy(update={"stages": (veto, motion, end)})

    change = _change([end], closure_detected=True, passed=True)

    assert update_event(change, vetoed) == "veto_sustained"


def test_the_senate_moving_rejection_is_named_as_one(process_1962: ProcessDetail) -> None:
    """The API says "wnosi o odrzucenie ustawy" and nothing else; the post used to be headed
    with the bare "senate" event, which says only that the Senate had spoken."""
    position = Stage(
        stage_type="SenatePosition",
        stage_name="Stanowisko Senatu",
        position="wnosi o odrzucenie ustawy",
        print_number="1352",
    )
    bill = _bill(process_1962).model_copy(update={"stages": (position,)})

    assert update_event(_change([position]), bill) == "senate_rejected"


def test_the_government_position_outweighs_the_other_filed_documents(
    process_1962: ProcessDetail,
) -> None:
    """A stage the arrival of the position also makes has no name of its own, so without this
    the post that carries the government's verdict would be headed "Обновление"."""
    stage = Stage(stage_type="GovermentPosition", stage_name="Wpłynęło stanowisko rządu")
    filed = [_filed("impact_assessment"), _filed("government_position")]
    change = _change([stage], supplements=filed)

    assert has_news(change)
    assert update_event(change, _bill(process_1962)) == "government_position"
    assert "government_position" in event_keys(change, "government_position")


def test_a_filed_document_alone_is_news_of_its_own(process_1962: ProcessDetail) -> None:
    change = _change([], supplements=[_filed("impact_assessment")])

    assert has_news(change)
    assert update_event(change, _bill(process_1962)) == "impact_assessment"


def test_a_bill_withdrawn_after_its_print_is_not_told_as_rejected(
    process_1962: ProcessDetail,
) -> None:
    submission = BillSubmission(
        term=10,
        number="RPW/1/2026",
        title=process_1962.title,
        status="WITHDRAWN",
        date_of_receipt=dt.date(2026, 1, 2),
    )
    withdrawn = _bill(process_1962, submission=submission).model_copy(update={"stages": ()})

    event = update_event(_change([], closure_detected=True, passed=False), withdrawn)

    assert event == "withdrawn_by_applicant"


def test_the_governments_position_names_the_post_even_when_its_document_was_not_read(
    process_1962: ProcessDetail,
) -> None:
    """`supplement_event` can only name the post when the filing was digested, and a scan, a
    refusal or a text over the per-bill limit all leave the digest empty. The stage arrives
    either way: 73 of the 6,260 stage transitions of term 10 went out as «Обновление» over the
    government's verdict on a bill it did not write."""
    bill = _bill(process_1962)
    position = Stage(stage_name="Wpłynęło stanowisko rządu", stage_type="GovermentPosition")

    assert update_event(_change([position]), bill) == "government_position"
    assert update_event(_change([position], supplements=[_filed("government_position")]), bill) == (
        "government_position"
    )


def test_an_opinion_filed_by_another_body_is_held_and_not_a_post_of_its_own() -> None:
    """Filed opinions are not this channel's genre (decided 2026-09-12), and `process_stages`
    already drops the stage as one that arrives beside the process — but it counted as news and
    got a post of its own, headed «Обновление»: 149 transitions of term 10, on 192 prints."""
    opinion = Stage(stage_name="Opinia organizacji samorządowej", stage_type="Opinion")
    referral = Stage(stage_name="Skierowano do komisji", stage_type="Referral")

    assert not is_substantive(opinion)
    assert not has_news(_change([opinion]))
    assert has_news(_change([opinion, referral]))


def _reading(decision: str) -> Stage:
    return Stage(
        stage_name="II czytanie na posiedzeniu Sejmu",
        stage_type="SejmReading",
        decision=decision,
        date=dt.date(2026, 9, 15),
    )


def _committee_work() -> Stage:
    return Stage(
        stage_name="Praca w komisjach po II czytaniu",
        stage_type="CommitteeWork",
        date=dt.date(2026, 9, 15),
        children=(
            Stage(
                stage_name="Sprawozdanie komisji",
                stage_type="CommitteeReport",
                proposal="przyjąć część poprawek",
                date=dt.date(2026, 9, 15),
            ),
        ),
    )


def test_a_stage_that_arrives_behind_one_already_told_fills_in_the_past() -> None:
    """Druk 1929, 15 Sept 2026: the committee's work after the second reading reached the tree at
    11:32 and the second reading itself at 12:29, standing before it. Named after its own newest
    stage, the second post announced the cause of the first."""
    start = Stage(stage_name="Projekt wpłynął", stage_type="Start", date=dt.date(2025, 11, 4))
    told = (start, _committee_work())
    found = (start, _reading("skierowano ponownie do komisji"), _committee_work())

    assert fills_in_the_past(told, found)


def test_a_stage_that_follows_what_was_told_is_told_at_once() -> None:
    """The same two nodes in the order the Sejm's tree puts them: nothing is held."""
    start = Stage(stage_name="Projekt wpłynął", stage_type="Start", date=dt.date(2025, 11, 4))
    reading = _reading("skierowano ponownie do komisji")

    assert not fills_in_the_past((start,), (start, reading))
    assert not fills_in_the_past((start, reading), (start, reading, _committee_work()))


def test_the_governments_position_is_not_a_stage_that_fills_in_the_past() -> None:
    """It arrives beside the process and lands in the tree wherever its date puts it — 196 of the
    241 such nodes of term 10 stand before a road stage dated later. Read as part of the road it
    would hold back the one filing this channel does tell."""
    reading = _reading("skierowano ponownie do komisji")
    position = Stage(
        stage_name="Stanowisko rządu",
        stage_type="GovermentPosition",
        date=dt.date(2026, 3, 1),
    )

    assert not fills_in_the_past((reading,), (position, reading))
