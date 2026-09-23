import datetime as dt

import pytest

from lexinform.models import (
    Bill,
    BillStatus,
    CommitteeSitting,
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    SejmTerm,
)
from tests.harness import (
    CHANNEL,
    COMMITTEE_STAGES,
    RCL,
    RPW,
    WYKAZ,
    World,
    rcl_document,
    rcl_folder,
    rcl_project,
    rcl_stage,
    submission,
    wykaz_entry,
)


def fail_delivery(change_id: int, channel_id: str, delivery: DeliveryPlan) -> None:
    raise RuntimeError("checkpoint interrupted")


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("source", ["rcl", "rpw", "wykaz"])
def test_source_observation_and_delivery_roll_back_together(
    source: str, workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = World(workers=workers)
    if source == "rcl":
        project = w.add_rcl_project()
        number = RCL
    elif source == "rpw":
        w.gateway.submissions.append(submission())
        number = RPW
    else:
        w.add_wykaz_entry()
        number = WYKAZ
    w.run()
    before = w.bill(number)
    w.clock.advance(days=1)
    if source == "rcl":
        w.rcl.put(
            project.model_copy(
                update={
                    "stages": (
                        *project.stages,
                        rcl_stage(9, "Stały Komitet Rady Ministrów", "active"),
                    ),
                    "modified": dt.date(2026, 9, 8),
                }
            )
        )
    elif source == "rpw":
        w.gateway.submissions[0] = submission(status="WITHDRAWN")
    else:
        w.add_wykaz_entry(wykaz_entry(status="Wycofany"))

    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_update_delivery", fail_delivery)
        w.run()

    after = w.bill(number)
    assert (after.rcl, after.submission, after.wykaz, after.stages_fingerprint) == (
        before.rcl,
        before.submission,
        before.wykaz,
        before.stages_fingerprint,
    )
    assert w.publication(number, PublicationKind.STATUS_UPDATE) is None
    assert w.repo.get_status_change(1) is None
    assert w.run().updates == 1
    assert w.run().updates == 0


def test_rcl_paid_analysis_survives_a_failed_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World()
    project = w.add_rcl_project()
    w.run()
    before = w.bill(RCL).analysis
    w.clock.advance(days=1)
    folder = rcl_folder(
        777, "Projekt", rcl_document(801, "projekt_po_KP.pdf", created=dt.date(2026, 9, 8))
    )
    w.add_rcl_project(
        project.model_copy(
            update={
                "stages": (*project.stages, rcl_stage(10, "Komisja Prawnicza", "active", folder)),
                "modified": dt.date(2026, 9, 8),
            }
        )
    )
    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_update_delivery", fail_delivery)
        w.run()

    assert w.bill(RCL).analysis == before
    assert len(w.llm.contexts) == 2
    w.repo.restore(w.repo.dump())
    recovered = w.run()
    assert recovered.updates == 1 and len(w.llm.contexts) == 2
    assert w.publisher.updates[0][1].content_changed


def test_link_checkpoint_keeps_the_predecessor_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    w.clock.advance(days=1)
    w.gateway.submissions[0] = submission(print_number="3100")
    w.add_bill("3100", "Projekt ustawy o cudzoziemcach")
    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_update_delivery", fail_delivery)
        w.tracking.check_updates()

    assert w.bill(RPW).status is not BillStatus.LINKED
    assert w.publication("3100") is None
    assert w.publication("3100", PublicationKind.STATUS_UPDATE) is None
    calls = len(w.llm.contexts)
    recovered = w.tracking.check_updates()
    assert recovered.linked == 1 and recovered.published == 1
    assert len(w.llm.contexts) == calls
    assert w.bill(RPW).status is BillStatus.LINKED


def test_rollover_checkpoint_cannot_discontinue_without_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.gateway.terms = [SejmTerm(num=11, start=dt.date(2027, 11, 13), current=True)]
    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_update_delivery", fail_delivery)
        w.run(term=None, expect_bugs=True)

    assert w.bill("3039").discontinued_at is None
    assert w.publication("3039", PublicationKind.STATUS_UPDATE) is None
    assert w.run(term=None).updates == 1
    assert w.bill("3039").discontinued_at is not None


def test_wykaz_link_failure_keeps_the_plan_and_reuses_the_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World()
    w.add_wykaz_entry()
    w.run()
    w.add_rcl_project(rcl_project(wykaz_number="UD408"))
    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_update_delivery", fail_delivery)
        w.run()

    assert w.bill(WYKAZ).status is not BillStatus.LINKED
    assert w.publication(RCL) is None
    calls = len(w.llm.contexts)
    assert w.run().linked == 1
    assert len(w.llm.contexts) == calls
    assert w.bill(WYKAZ).status is BillStatus.LINKED
    assert w.publication(RCL) is not None


def test_failed_government_card_keeps_its_plan_after_term_rollover() -> None:
    w = World(fail_publish={RCL})
    w.add_rcl_project()
    w.run()
    row = w.publication(RCL)
    assert row is not None and row.delivery is not None, "a failed card keeps its row and plan"
    original = row.delivery.bill_json
    w.repo.move_government_rows(10, 11)
    w.publisher.fail_on.clear()

    w.run(term=11)

    assert len(w.publisher.new_bills) == 1
    assert w.publisher.new_bills[0][0].model_dump_json() == original
    assert w.repo.get_publication(10, RCL, PublicationKind.NEW_BILL, CHANNEL) is None
    moved = w.repo.get_publication(11, RCL, PublicationKind.NEW_BILL, CHANNEL)
    assert moved is not None and moved.status is PublicationStatus.SENT


def test_failed_card_retries_its_saved_facts_after_restore() -> None:
    w = World(fail_publish={"3039"})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    row = w.publication("3039")
    assert row is not None and row.delivery is not None, "a failed card keeps its row and plan"
    original = row.delivery
    bill = w.bill("3039")
    assert bill.analysis is not None, "the card was rendered from an analysis"
    replacement = bill.analysis.model_copy(deep=True)
    replacement.analysis.summary = "Позднейший текст"
    w.repo.save_analysis(10, "3039", replacement)
    w.repo.restore(w.repo.dump())
    w.publisher.fail_on.clear()

    assert w.run().published == 1
    assert w.publisher.new_bills[0][0].model_dump_json() == original.bill_json
    assert w.run().published == 0


def test_failed_reminder_keeps_the_date_facts_and_thread_after_deadline() -> None:
    w = World()
    w.gateway.submissions.append(submission(consultation_end=dt.date(2026, 9, 20)))
    w.run()
    card_id = w.card_id(RPW)
    w.clock.advance(days=10)
    w.publisher.fail_on.add(RPW)
    w.run()
    row = w.publication(RPW, PublicationKind.CONSULTATION_DEADLINE)
    assert row is not None and row.delivery is not None, "a failed reminder keeps its row and plan"
    snapshot = row.delivery.bill_json
    w.clock.advance(days=10)
    w.publisher.fail_on.clear()
    w.repo.restore(w.repo.dump())

    report = w.run()

    bill, reply, today = w.publisher.consultations[0]
    assert report.consultation_reminders == 1
    assert (bill.model_dump_json(), reply, today) == (snapshot, card_id, dt.date(2026, 9, 17))
    assert w.run().consultation_reminders == 0


def test_agenda_failure_preserves_the_previous_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.run()
    w.gateway.committee_sittings["ASW"] = (
        CommitteeSitting(
            code="ASW",
            num=136,
            date=dt.date(2026, 9, 17),
            status="PLANNED",
            agenda="Pierwsze czytanie (druk nr 3039)",
        ),
    )
    original_create = w.repo.create_publication

    def fail_agenda(publication: Publication) -> int:
        if publication.kind is PublicationKind.AGENDA:
            raise RuntimeError("agenda checkpoint interrupted")
        return original_create(publication)

    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "create_publication", fail_agenda)
        w.run()

    assert w.bill("3039").agenda == ()
    assert w.run().agenda_posted == 1
    assert w.run().agenda_posted == 0


def test_queued_agenda_is_recovered_after_the_sitting_disappears() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=COMMITTEE_STAGES)
    w.run()
    w.gateway.committee_sittings["ASW"] = (
        CommitteeSitting(
            code="ASW",
            num=136,
            date=dt.date(2026, 9, 17),
            status="PLANNED",
            agenda="Pierwsze czytanie (druk nr 3039)",
        ),
    )
    w.run(publish=False)
    item = w.bill("3039").agenda[0]
    row = w.repo.get_publication(10, "3039", PublicationKind.AGENDA, CHANNEL, ref=item.ref)
    assert row is not None and row.delivery is not None, "an unpublished run queues the agenda post"
    assert row.status is PublicationStatus.QUEUED
    original = Bill.model_validate_json(row.delivery.bill_json)
    w.gateway.committee_sittings["ASW"] = ()
    w.repo.restore(w.repo.dump())

    report = w.run()

    assert (report.agenda_posted, report.agenda_cancelled) == (1, 1)
    assert w.publisher.agendas[0][0] == original
    assert w.publisher.agendas[0][1] == item
