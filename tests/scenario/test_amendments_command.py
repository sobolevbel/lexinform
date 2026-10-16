import datetime as dt
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lexinform.cli import app
from lexinform.models import (
    Attachment,
    Bill,
    DigestItemMeta,
    OutcomeStatus,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    parse_command,
    stage_fingerprint,
)
from tests.fakes import FakeTextExtractor, make_amendments
from tests.harness import REFERRED, World

PDF_URL = "https://api.test/sejm/term10/prints/3213/3213.pdf"
WORD_URL = "https://api.test/sejm/term10/prints/3213/3213-uchwa%C5%82a.docx"
SENATE = Stage(
    stage_type="SenatePosition",
    stage_name="Stanowisko Senatu",
    date=dt.date(2026, 9, 8),
    position="wniósł poprawki",
    print_number="3213",
)


def published_world(*, workers: int = 1, attention: bool = False) -> tuple[World, Publication]:
    fixtures = Path(__file__).parents[1] / "fixtures/sejm"
    extractor = FakeTextExtractor(
        by_content={
            b"cover": (fixtures / "senate_3213_cover.txt").read_text(),
            b"word": (fixtures / "senate_3213_resolution.txt").read_text(),
        }
    )
    w = World(
        extractor=extractor,
        workers=workers,
        text_budget_chars=60_000,
        reader_attention="on" if attention else "off",
    )
    w.add_bill("1929", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.gateway.prints["3213"] = PrintInfo(
        term=10,
        number="3213",
        title="Uchwała Senatu",
        attachments=(Attachment(print_number="3213", name="3213.pdf", url=PDF_URL),),
    )
    w.gateway.files[PDF_URL] = b"cover"
    w.llm.amendments_script["1929"] = make_amendments(summary="Текст поправок отсутствует")
    w.set_stages("1929", REFERRED + (SENATE,))
    w.clock.advance(days=1)
    w.run()
    change = w.repo.list_status_changes(10, "1929")[-1]
    assert change.id is not None
    original = w.repo.get_update_publication(change.id, "@test")
    assert original is not None and original.status is PublicationStatus.SENT
    w.gateway.prints["3213"] = w.gateway.prints["3213"].model_copy(
        update={
            "attachments": (
                *w.gateway.prints["3213"].attachments,
                Attachment(print_number="3213", name="3213-uchwała.docx", url=WORD_URL),
            )
        }
    )
    w.gateway.files[WORD_URL] = b"word"
    w.batch.secondary_script[("1929", "amendments")] = make_amendments(
        summary="Исправленный разбор поправок Сената"
    )
    return w, original


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("attention", [False, True])
def test_amendments_command_batches_full_resolution_and_edits_saved_message(
    workers: int, attention: bool
) -> None:
    w, original = published_world(workers=workers, attention=attention)
    assert original.delivery is not None and original.id is not None
    saved_bill = Bill.model_validate_json(original.delivery.bill_json)
    before = w.bill("1929")
    sent = len(w.publisher.sent)
    w.command("/amendments 1929")

    first = w.run(track=False)
    w.command("/amendments 1929 force")
    w.run(track=False)

    assert first.commands_failed == 0
    assert w.replier.replies[-1][1].status is OutcomeStatus.QUEUED
    assert len(w.batch.submitted) == 1
    context = w.batch.submitted[0].ctx
    assert "wprowadza do jej tekstu następujące poprawki" in context.model_dump_json()
    assert "Szanowny Panie Marszałku" not in context.model_dump_json()
    assert len(w.llm.amendment_contexts) == 1
    assert len(w.publisher.sent) == sent
    w.repo.restore(w.repo.dump())
    w.batch.resolve()
    collected = w.run()

    revisions = w.repo.list_amendment_revisions(original.id)
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision.status is PublicationStatus.SENT
    assert revision.message_id == original.message_id
    assert revision.delivery is not None
    revised_change = StatusChange.model_validate_json(revision.delivery.change_json)
    assert revised_change.amendments is not None
    assert revised_change.amendments.source_url == WORD_URL
    assert revised_change.amendments.amendments.summary == "Исправленный разбор поправок Сената"
    assert revision.delivery.bill_json == original.delivery.bill_json
    assert revision.delivery.today == original.delivery.today
    assert collected.amendment_messages_updated == 1
    assert len(w.publisher.sent) == sent
    message, identity = next(
        (m, identity)
        for m, identity in w.publisher.edited
        if m.kind is PublicationKind.AMENDMENTS_REVISION
    )
    assert identity == original.message_id
    assert "Исправленный разбор" in message.text and "Текст поправок (DOCX)" in message.text
    assert w.repo.publication_by_id(original.id) == original
    assert w.bill("1929").analysis == before.analysis == saved_bill.analysis
    assert w.bill("1929").observed_process == before.observed_process
    w.command("/amendments 1929")
    w.run()
    assert len(w.batch.submitted) == 1
    assert len(w.repo.list_amendment_revisions(original.id)) == 1


def test_force_queues_new_revision_only_after_previous_delivery_completed() -> None:
    w, original = published_world()
    assert original.id is not None
    w.command(f"/amendments 1929 publication={original.id}")
    w.run()
    w.batch.resolve()
    w.run()
    w.command("/amendments 1929 force")

    w.run()

    assert len(w.batch.submitted) == 2
    w.batch.resolve()
    w.run()
    assert len(w.repo.list_amendment_revisions(original.id)) == 2


@pytest.mark.parametrize("status", [PublicationStatus.UNKNOWN, PublicationStatus.FAILED])
def test_pending_delivery_blocks_new_analysis_and_preserves_payload(
    status: PublicationStatus,
) -> None:
    w, original = published_world()
    assert original.id is not None
    w.command("/amendments 1929")
    w.run()
    w.batch.resolve()
    if status is PublicationStatus.UNKNOWN:
        w.publisher.unknown_on_edit.add("1929")
    else:
        w.publisher.fail_on.add("1929")
    w.run()
    revision = w.repo.list_amendment_revisions(original.id)[0]
    assert revision.id is not None
    assert revision.status is status
    before = w.repo.publication_by_id(revision.id)
    w.command("/amendments 1929 force")

    w.run(track=False)

    assert w.repo.publication_by_id(revision.id) == before
    assert len(w.batch.submitted) == 1
    assert f"/delivery {revision.id}" in w.replier.replies[-1][1].note


def test_explicit_publication_cannot_edit_another_bill() -> None:
    w, original = published_world()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.command(f"/amendments 3039 publication={original.id}")

    w.run()

    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert not w.batch.submitted


def test_newer_bill_stages_do_not_leak_into_the_historical_correction() -> None:
    w, original = published_world()
    assert original.id is not None and original.delivery is not None
    w.command("/amendments 1929")
    w.run(track=False)
    later = Stage(
        stage_type="PresidentSignature",
        stage_name="Prezydent podpisał ustawę",
        date=dt.date(2026, 10, 16),
    )
    current_stages = w.bill("1929").stages + (later,)
    w.repo.save_stages(10, "1929", current_stages, stage_fingerprint(current_stages))
    w.clock.advance(days=10)
    w.batch.resolve()

    w.analysis.collect_batches()
    report = w.container.delivery_service(dry_run=False).run()

    revision = w.repo.list_amendment_revisions(original.id)[0]
    assert report.amendment_messages_updated == 1
    assert revision.delivery is not None
    assert revision.delivery.bill_json == original.delivery.bill_json
    assert later in w.bill("1929").stages
    edited = [
        m.text for m, _ in w.publisher.edited if m.kind is PublicationKind.AMENDMENTS_REVISION
    ]
    assert len(edited) == 1 and "Президент подписал" not in edited[0]


def test_uncertain_batch_submission_is_not_replaced_by_force() -> None:
    w, _ = published_world()
    w.command("/amendments 1929")
    w.run(dry_run=True, track=False)
    assert not w.batch.submitted and not w.repo.list_queued_batch_intents()
    service = w.container.amendments_service()
    service.request(w.bill("1929"))
    intent = w.repo.list_queued_batch_intents()[0]
    w.repo.mark_batch_intents_submitting([intent.request.custom_id])

    outcome = service.request(w.bill("1929"), force_id="new-command")

    assert outcome.status is OutcomeStatus.QUEUED
    assert len(w.repo.list_submitting_batch_intents()) == 1
    assert not w.repo.list_queued_batch_intents()


def test_failed_model_answer_keeps_original_and_can_be_requeued_as_batch() -> None:
    w, original = published_world()
    w.command("/amendments 1929")
    w.run()
    w.batch.secondary_script[("1929", "amendments")] = RuntimeError("provider refused")
    w.batch.resolve()
    w.run()
    assert original.id is not None
    assert not w.repo.list_amendment_revisions(original.id)
    assert w.repo.publication_by_id(original.id) == original
    w.batch.secondary_script[("1929", "amendments")] = make_amendments()
    w.command("/amendments 1929")

    w.run()

    assert len(w.batch.submitted) == 2
    assert len(w.llm.amendment_contexts) == 1


def test_cli_queues_and_submits_the_same_amendment_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w, original = published_world()
    monkeypatch.setattr("lexinform.cli._container", lambda: w.container)

    result = CliRunner().invoke(app, ["amendments", "1929", "--publication", str(original.id)])

    assert result.exit_code == 0, result.output
    assert len(w.batch.submitted) == 1
    assert "ожидают batch" in result.output


def test_amendments_options_are_validated() -> None:
    command = parse_command("/amendments 1929 publication=136 force")
    assert command is not None and not command.error and command.force
    assert command.count("publication", 0) == 136
    for text in ("/amendments 1929 publication=0", "/amendments 1929 publish"):
        invalid = parse_command(text)
        assert invalid is not None and invalid.error


def test_legacy_message_without_snapshot_is_refused_before_model_call() -> None:
    w, original = published_world()
    future = w.clock.now() + dt.timedelta(days=365)
    w.repo.prune_history(before=future, memo_before=future)
    w.command(f"/amendments 1929 publication={original.id}")

    w.run(track=False)

    outcome = w.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.ERROR and "снимок" in outcome.note
    assert not w.batch.submitted


def test_revision_audit_survives_retention_and_model_collection_is_idempotent() -> None:
    w, original = published_world()
    assert original.id is not None
    w.command("/amendments 1929")
    w.run()
    w.batch.resolve()
    w.run()
    before = w.repo.list_amendment_revisions(original.id)
    future = w.clock.now() + dt.timedelta(days=365)

    w.repo.prune_history(before=future, memo_before=future)
    w.analysis.collect_batches()

    assert w.repo.list_amendment_revisions(original.id) == before
    assert before[0].delivery is not None


def test_long_revision_freezes_continuations_and_waits_for_the_edit() -> None:
    w, original = published_world()
    assert original.id is not None
    w.batch.secondary_script[("1929", "amendments")] = make_amendments(
        summary="Изменения правил получения асистенции. " * 250
    )
    w.command("/amendments 1929")
    w.run()
    w.batch.resolve()
    w.publisher.fail_on.add("1929")
    w.run()
    revision = w.repo.list_amendment_revisions(original.id)[0]
    assert revision.delivery is not None and revision.delivery.continuation_ids
    assert revision.status is PublicationStatus.FAILED
    children = [w.repo.publication_by_id(i) for i in revision.delivery.continuation_ids]
    assert all(p is not None and p.status is PublicationStatus.QUEUED for p in children)
    assert not any(p.kind is PublicationKind.MESSAGE_PART for p in w.publisher.sent)
    frozen = revision.delivery
    w.publisher.fail_on.clear()

    w.run()

    delivered = w.repo.list_amendment_revisions(original.id)[0]
    assert delivered.delivery == frozen
    assert delivered.status is PublicationStatus.SENT
    assert all(
        (p := w.repo.publication_by_id(i)) is not None and p.status is PublicationStatus.SENT
        for i in frozen.continuation_ids
    )
    w.command("/amendments 1929 force")
    w.run()
    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert len(w.batch.submitted) == 1


def test_unreadable_new_source_is_not_analysed_as_an_empty_amendment() -> None:
    w, _ = published_world()
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[b"word"] = ""
    w.command("/amendments 1929")

    w.run()

    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert not w.batch.submitted


def test_unknown_continuation_is_reported_instead_of_completed_root() -> None:
    w, original = published_world()
    assert original.id is not None
    w.batch.secondary_script[("1929", "amendments")] = make_amendments(
        summary="Изменения правил получения асистенции. " * 250
    )
    w.command("/amendments 1929")
    w.run()
    w.batch.resolve()
    w.publisher.unknown_continuation_attempt = 1
    w.run()
    revision = w.repo.list_amendment_revisions(original.id)[0]
    assert revision.status is PublicationStatus.SENT and revision.delivery is not None
    identity = revision.delivery.continuation_ids[0]
    part = w.repo.publication_by_id(identity)
    assert part is not None and part.status is PublicationStatus.UNKNOWN
    w.command("/amendments 1929")

    w.run()

    outcome = w.replier.replies[-1][1]
    assert outcome.status is OutcomeStatus.QUEUED
    assert "unknown" in outcome.note and f"/delivery {identity}" in outcome.note
    assert len(w.batch.submitted) == 1


@pytest.mark.parametrize("multipart", [False, True])
def test_shared_or_multipart_original_is_refused_before_source_read(multipart: bool) -> None:
    w, original = published_world()
    assert original.delivery is not None
    change = StatusChange.model_validate_json(original.delivery.change_json).model_copy(
        update={"id": None, "new_fingerprint": "another-amendments-event"}
    )
    change_id = w.repo.add_status_change(change)
    assert change_id is not None
    delivery = original.delivery.model_copy(
        update={"continuation_ids": (999,)} if multipart else {"absorbed_by": 999}
    )
    identity = w.repo.create_publication(
        original.model_copy(
            update={"id": None, "status_change_id": change_id, "delivery": delivery}
        )
    )
    w.command(f"/amendments 1929 publication={identity}")

    w.run(track=False)

    assert w.replier.replies[-1][1].status is OutcomeStatus.ERROR
    assert not w.batch.submitted


def test_committee_amendment_revision_uses_the_historical_report_and_proposal() -> None:
    w, original = published_world()
    report_url = "https://api.test/sejm/term10/prints/2792-A/2792-A.pdf"
    report = Stage(
        stage_type="CommitteeReport",
        stage_name="Sprawozdanie komisji",
        date=dt.date(2026, 9, 9),
        print_number="2792-A",
        report_file=report_url,
        proposal="przyjąć część poprawek",
    )
    w.gateway.files[report_url] = b"%PDF"
    w.set_stages("1929", REFERRED + (SENATE, report))
    w.clock.advance(days=1)
    w.run()
    change = w.repo.list_status_changes(10, "1929")[-1]
    assert change.id is not None
    latest = w.repo.get_update_publication(change.id, "@test")
    assert latest is not None and latest.id != original.id
    w.command("/amendments 1929")

    w.run(track=False)

    request = w.batch.submitted[0]
    assert request.ctx.model_dump()["source_kind"] == "committee_amendments"
    assert request.ctx.model_dump()["proposal"] == "przyjąć część poprawek"
    item = w.repo.list_llm_batch_items(w.repo.list_open_llm_batches()[0].batch_id)[0]
    assert isinstance(item.meta, DigestItemMeta)
    assert item.meta.amendment_revision is not None
    assert item.meta.amendment_revision.source_url == report_url
    assert item.meta.amendment_revision.publication_id == latest.id
