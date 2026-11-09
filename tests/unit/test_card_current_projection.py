from datetime import datetime, timedelta

import pytest

from lexinform.input_receipts import primary_input_receipt
from lexinform.models import (
    AnalysisRecord,
    ArtifactMetadata,
    BatchIntent,
    Bill,
    ComputationInputBinding,
    ComputationResult,
    DocumentSelectionRecord,
    LlmBatch,
    LlmBatchItem,
    PublicationKind,
    PublicationStatus,
    ResultApplication,
    SelectionArtifactReference,
    SelectionExtractionReference,
    computation_id,
)
from lexinform.models.content import CardPrimaryEvidence
from lexinform.primary_projections import primary_record_sha256
from lexinform.services.card_edits import edit_card
from lexinform.services.publishing import PublishingService
from tests.fakes import FakePublisher, FakeSejmGateway, FixedClock
from tests.unit.test_card_content_writer import MultipartPublisher, renamed
from tests.unit.test_card_primary_evidence_reader import (
    card_parents,
    compact_revision_candidate,
    store_marked_card,
)
from tests.unit.test_content_sources import Case
from tests.unit.test_content_sources import case as case
from tests.unit.test_input_receipts import NOW, SOURCE, meta, request, selection
from tests.unit.test_physical_extraction import docx, extractor


def changed_paid_card(case: Case, paid_at: datetime) -> tuple[Bill, CardPrimaryEvidence]:
    repo = case.repo
    text = SOURCE.replace("300 zł", "400 zł")
    raw = docx(text)
    observed = extractor().extract_document_with_provenance(raw)
    artifact = ArtifactMetadata(
        sha256=observed.result.artifact_sha256, byte_size=len(raw), recorded_at=paid_at
    )
    draft = selection(text)
    part = draft.parts[0].model_copy(update={"sha256": artifact.sha256, "text_end": len(text)})
    selected = draft.model_copy(update={"parts": (part,)})
    retained = DocumentSelectionRecord.from_selection(selected)
    observation = case.observation.model_copy(
        update={"selection_id": retained.selection_id, "recorded_at": paid_at}
    )
    repo.save_artifact_metadata(artifact)
    repo.save_extraction_result(observed.result, recorded_at=paid_at)
    repo.save_source_observation(retained, observation)
    repo.save_selection_artifact(
        SelectionArtifactReference(
            selection_id=retained.selection_id,
            part_scope="parts",
            part_index=0,
            artifact_sha256=artifact.sha256,
        )
    )
    repo.save_selection_extraction(
        SelectionExtractionReference(
            selection_id=retained.selection_id,
            extraction_id=observed.result.result_id,
            artifact_sha256=artifact.sha256,
        )
    )
    frozen = request(text).model_copy(update={"custom_id": "new-card-input"})
    receipt = primary_input_receipt(
        frozen,
        selected,
        projection=None,
        known_selection=retained,
        body=artifact,
        extraction=observed.result,
    )
    binding = ComputationInputBinding(term=10, number="3039", reuse_key="new-card-input")
    intent = BatchIntent.from_request(
        request=frozen,
        meta=meta(receipt).model_copy(
            update={
                "source_url": part.url,
                "selection": retained.selection,
                "generation": 4,
                "text": text,
                "memo_key": binding.reuse_key,
            }
        ),
        provider="openai",
        created_at=paid_at,
    )
    batch_id = "new-card-evidence"
    item = LlmBatchItem.from_intent(batch_id, intent)
    repo.save_batch_intent(intent)
    repo.save_llm_batch(
        LlmBatch(
            batch_id=batch_id,
            provider="openai",
            call_kind="analysis",
            submitted_at=paid_at,
            status="ended",
            request_count=1,
            estimated_cost_usd=0.01,
        ),
        [item],
    )
    repo.delete_batch_intents([item.custom_id])
    repo.mark_llm_batch_item_consumed(batch_id, item.custom_id, consumed_at=paid_at)
    repo.mark_batch_item_accounted(batch_id, item.custom_id, at=paid_at)
    repo.mark_llm_batch_forgotten(batch_id, forgotten_at=paid_at)
    original = case.paid.record
    assert isinstance(original, AnalysisRecord), "the fixture uses a typed primary result"
    answer = original.model_copy(
        update={
            "computation_id": computation_id("openai", batch_id, item.custom_id),
            "request_sha256": frozen.payload_sha256,
            "created_at": paid_at,
            "analysis": original.analysis.model_copy(
                update={"summary": "Now the amount is 400 PLN."}
            ),
        }
    )
    paid = ComputationResult.model_validate(
        {
            **case.paid.model_dump(),
            "batch_id": batch_id,
            "custom_id": item.custom_id,
            "generation": 4,
            "request_sha256": frozen.payload_sha256,
            "collected_at": paid_at,
            "input_binding": binding,
            "record": answer,
        }
    )
    repo.save_computation_result(paid)
    collection = ResultApplication(
        result_id=paid.result_id,
        term=10,
        number="3039",
        generation=4,
        outcome="ready",
        reason="current_generation",
        observed_generation=4,
        observed_status=None,
        created_at=paid_at,
    )
    repo.save_result_application(collection)
    repo.save_result_application(
        collection.model_copy(
            update={
                "phase": "projection",
                "reason": "batch_result",
                "outcome": "applied",
                "projected_generation": 5,
            }
        )
    )
    bill = case.bill.model_copy(update={"analysis": answer, "analysis_generation": 5})
    reference = repo.find_card_primary_evidence(bill)
    assert reference is not None, "the new fixture has a distinct paid projection and exact input"
    return bill, reference.model_copy(
        update={
            "revision_analysis_sha256": primary_record_sha256(answer),
            "revision_projection": "current",
        }
    )


@pytest.mark.parametrize("projection_after_predecessor", [False, True])
def test_reader_requires_current_paid_projection_and_its_confirmed_predecessor(
    case: Case, projection_after_predecessor: bool
) -> None:
    root, previous = store_marked_card(case)
    assert root.id is not None, "the fixture retains an original physical card"
    sent_at = NOW + timedelta(days=1)
    case.repo.mark_publication(root.id, PublicationStatus.SENT, message_id=10, sent_at=sent_at)
    paid_at = NOW + timedelta(days=2) if projection_after_predecessor else NOW
    bill, reference = changed_paid_card(case, paid_at)
    at = NOW + timedelta(days=3)
    identity, plain = compact_revision_candidate(
        case, root, previous, root.id, bill, at, "New edition"
    )
    unchanged = case.repo.dump()
    marked = plain.model_copy(update={"card_primary_evidence": reference})
    if not projection_after_predecessor:
        with pytest.raises(ValueError, match="card_primary_chronology"):
            case.repo.save_content_version(marked)
        assert case.repo.dump() == unchanged
        return
    for wrong in (
        reference.model_copy(update={"revision_analysis_sha256": "0" * 64}),
        reference.model_copy(update={"source_observation_id": "source-observation-v1:" + "0" * 64}),
    ):
        with pytest.raises(ValueError):
            case.repo.save_content_version(
                plain.model_copy(update={"card_primary_evidence": wrong})
            )
        assert case.repo.dump() == unchanged
    case.repo.save_content_version(marked)
    case.repo.bind_publication_content(identity, marked.content_id)
    case.repo.mark_publication(identity, PublicationStatus.SENT, message_id=10, sent_at=at)
    case.repo.prune_history(before=at + timedelta(days=1), memo_before=NOW)
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql
    assert case.repo.get_publication_content(root.id) == previous
    assert case.repo.get_publication_content(identity) == marked


@pytest.mark.parametrize("exact_projection", [False, True])
@pytest.mark.parametrize("unmarked_generation", [None, 3, 5])
def test_real_card_refresh_marks_new_paid_analysis_only_with_exact_projection(
    case: Case, exact_projection: bool, unmarked_generation: int | None
) -> None:
    original, previous = store_marked_card(case)
    assert original.id is not None, "the fixture retains the original physical card"
    case.repo.mark_publication(
        original.id, PublicationStatus.SENT, message_id=10, sent_at=NOW + timedelta(days=1)
    )
    paid_at = NOW + timedelta(days=2) if exact_projection else NOW
    bill, reference = changed_paid_card(case, paid_at)
    bill = renamed(bill)
    publisher = FakePublisher()
    confirmed = previous
    if unmarked_generation is not None:
        assert bill.analysis is not None, "the fixture uses an actual paid answer"
        unbound_analysis = bill.analysis.model_copy(update={"model": "historical-unbound"})
        unmarked = bill.model_copy(
            update={"analysis": unbound_analysis, "analysis_generation": unmarked_generation}
        )
        root = case.repo.publication_by_id(original.id)
        assert root is not None, "the historical edit targets the same physical message"
        predecessor_at = NOW + timedelta(days=1, hours=1)
        result = edit_card(
            case.repo,
            FixedClock(predecessor_at),
            publisher,
            unmarked,
            root,
            None,
            channel_id=root.channel_id,
            today=predecessor_at.date(),
        )
        assert result.completed
        confirmed = case.repo.list_content_versions(original.id)[-1]
        assert confirmed.previous_version_id == previous.content_id
        assert confirmed.card_primary_evidence is None
    historical = confirmed
    for step in (3, 4):
        at = NOW + timedelta(days=step)
        root = case.repo.publication_by_id(original.id)
        assert root is not None, "each edition targets the original physical message"
        title = bill.editorial.mnemonic
        assert title is not None, "the test makes an ordinary visible metadata change"
        edition = bill.model_copy(
            update={
                "editorial": bill.editorial.model_copy(
                    update={
                        "mnemonic": title.model_copy(update={"name": f"Paid edition {step}"}),
                    }
                ),
            }
        )
        expected = publisher.render_card_parts(edition, None, minimum=1, today=at.date())
        assert len(expected) == 1
        result = edit_card(
            case.repo,
            FixedClock(at),
            publisher,
            edition,
            root,
            None,
            channel_id=root.channel_id,
            today=at.date(),
        )
        assert result.completed
        assert (publisher.edited[-1][0].text, publisher.edited[-1][1]) == (expected[0], 10)
        versions = case.repo.list_content_versions(original.id)
        current = versions[-1]
        assert current.previous_version_id == confirmed.content_id
        assert current.card_primary_evidence == (
            reference if exact_projection and unmarked_generation != 5 else None
        )
        assert versions[0] == previous
        assert historical in versions
        case.repo.prune_history(before=at + timedelta(days=1), memo_before=NOW)
        sql = case.repo.dump()
        case.repo.restore(sql)
        assert case.repo.dump() == sql
        confirmed = current


@pytest.mark.parametrize("profile", ["exact", "early", "same_generation"])
def test_multipart_refresh_marks_new_paid_analysis_only_after_confirmed_predecessor(
    case: Case, profile: str
) -> None:
    original, previous = store_marked_card(case)
    assert original.id is not None, "the fixture retains the original physical card"
    case.repo.mark_publication(
        original.id, PublicationStatus.SENT, message_id=10, sent_at=NOW + timedelta(days=1)
    )
    paid_at = NOW + timedelta(days=3 if profile != "early" else 1, hours=1)
    bill, reference = changed_paid_card(case, paid_at)
    assert bill.analysis is not None, "the fixture retains the changed answer"
    historical_bill = renamed(
        bill.model_copy(
            update={
                "analysis": bill.analysis.model_copy(update={"model": "historical-unbound"}),
                "analysis_generation": 5 if profile == "same_generation" else 3,
            }
        )
    )
    publisher = MultipartPublisher(publication_by_id=case.repo.publication_by_id)
    root = case.repo.publication_by_id(original.id)
    assert root is not None, "both refreshes target the same card"
    history_at = NOW + timedelta(days=2)
    historic_outcome = edit_card(
        case.repo,
        FixedClock(history_at),
        publisher,
        historical_bill,
        root,
        None,
        channel_id=root.channel_id,
        today=history_at.date(),
    )
    assert historic_outcome.completed
    historical = case.repo.list_content_versions(original.id)[-1]
    assert historical.previous_version_id == previous.content_id
    assert historical.multipart_card_revision is not None
    assert historical.card_primary_evidence is None
    prior_targets = tuple(row.message_id for row in case.repo.card_refresh_parts(original.id))
    assert len(prior_targets) == len(set(prior_targets)) == 2
    current_at = NOW + timedelta(days=4)
    expected = publisher.render_card_parts(bill, None, minimum=2, today=current_at.date())
    assert len(expected) == 2
    outcome = edit_card(
        case.repo,
        FixedClock(current_at),
        publisher,
        bill,
        root,
        None,
        channel_id=root.channel_id,
        today=current_at.date(),
    )
    assert outcome.completed
    assert tuple((message.text, target) for message, target in publisher.edited[-2:]) == tuple(
        zip(expected, prior_targets, strict=True)
    )
    assert len(publisher.sent) == 1
    versions = case.repo.list_content_versions(original.id)
    assert versions[:2] == [previous, historical]
    current = versions[-1]
    assert current.previous_version_id == historical.content_id
    assert current.multipart_card_revision is not None
    assert current.card_primary_evidence == (reference if profile == "exact" else None)
    case.repo.prune_history(before=current_at + timedelta(days=1), memo_before=NOW)
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql
    assert case.repo.list_content_versions(original.id) == versions


@pytest.mark.parametrize("complete_receipt", [False, True])
def test_first_multipart_card_retains_exact_paid_proof_only_with_complete_receipt(
    case: Case, complete_receipt: bool
) -> None:
    provisional, reference = card_parents(case)
    assert provisional.delivery is not None, "the fixture supplies the frozen paid Bill"
    bill = Bill.model_validate_json(provisional.delivery.bill_json)
    case.repo.delete_publication(
        bill.term, bill.number, PublicationKind.NEW_BILL, provisional.channel_id
    )
    if not complete_receipt:
        case.execute(
            "DELETE FROM result_applications WHERE phase='collection' AND result_id=("
            "SELECT result_id FROM result_applications WHERE id=?)",
            (reference.projection_application_id,),
        )
    at = NOW + timedelta(days=1)
    publisher = MultipartPublisher(publication_by_id=case.repo.publication_by_id)
    publishing = PublishingService(
        FakeSejmGateway(),
        case.repo,
        publisher,
        FixedClock(at),
        channel_id=provisional.channel_id,
        attention_assess=False,
    )
    assert publishing.publish_bill(bill)
    original = publishing.card_of(bill)
    assert original is not None and original.id is not None, "the first send has a physical root"
    history = case.repo.get_publication_content(original.id)
    assert history is not None and history.original_delivery_evidence is not None
    assert history.card_primary_evidence == (reference if complete_receipt else None)
    assert original.delivery is not None, "the freeze retains the physical tail"
    (child_id,) = original.delivery.continuation_ids
    child = case.repo.publication_by_id(child_id)
    assert child is not None and child.status is PublicationStatus.SENT
    assert case.repo.get_publication_content(child_id) == history
    expected = publisher.render_card_parts(bill, None, minimum=1, today=at.date())
    assert tuple(item.text for item in publisher.sent) == expected
    assert publisher.sent[-1].reply_to == original.message_id
    case.repo.prune_history(before=at + timedelta(days=1), memo_before=NOW)
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql
    assert case.repo.get_publication_content(original.id) == history
    assert case.repo.get_publication_content(child_id) == history
    revised_at = at + timedelta(days=2)
    outcome = edit_card(
        case.repo,
        FixedClock(revised_at),
        publisher,
        renamed(bill),
        original,
        None,
        channel_id=original.channel_id,
        today=revised_at.date(),
    )
    assert outcome.completed
    versions = case.repo.list_content_versions(original.id)
    assert versions[0] == history
    assert versions[-1].previous_version_id == history.content_id
    assert versions[-1].card_primary_evidence == (
        reference.model_copy(
            update={
                "revision_analysis_sha256": primary_record_sha256(bill.analysis),
                "revision_projection": "current",
            }
        )
        if complete_receipt and bill.analysis is not None
        else None
    )
    assert tuple(target for _, target in publisher.edited[-2:]) == (
        original.message_id,
        child.message_id,
    )
    case.repo.prune_history(before=revised_at + timedelta(days=1), memo_before=NOW)
    sql = case.repo.dump()
    case.repo.restore(sql)
    assert case.repo.dump() == sql
