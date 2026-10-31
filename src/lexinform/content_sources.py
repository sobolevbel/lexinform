"""Audit one frozen primary input chain without recording content or activating a producer."""

import json
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from lexinform.models import (
    AnalysisRecord,
    BatchItemMeta,
    BatchProvider,
    Bill,
    ComputationResult,
    ContentSnapshot,
    DocumentSelectionRecord,
    LlmBatchItem,
    Publication,
    PublicationKind,
    SourceObservation,
    StatusChange,
    amendments_stage,
)
from lexinform.models.primary_projections import PRIMARY_USAGE_FIELDS
from lexinform.primary_projections import primary_record_matches

SOURCE_CONTEXT_INVALID = "source_context_invalid"


class ContentSourceReader(Protocol):
    def get_content_snapshot(self, snapshot_id: str) -> ContentSnapshot | None: ...
    def get_computation_result(self, result_id: str) -> ComputationResult | None: ...
    def get_consumed_primary_input(
        self, provider: BatchProvider, batch_id: str, custom_id: str, generation: int
    ) -> LlmBatchItem | None: ...
    def get_document_selection(self, selection_id: str) -> DocumentSelectionRecord | None: ...
    def get_source_observation(self, observation_id: str) -> SourceObservation | None: ...
    def source_observations_for_selection(
        self, term: int, number: str, selection_id: str
    ) -> list[SourceObservation]: ...


@dataclass(frozen=True)
class ContentSourceAudit:
    state: Literal["known", "unknown", "invalid"]
    reason: str
    source_observation_ids: tuple[str, ...] = ()
    scope: Literal["frozen_primary_context/analysis"] = "frozen_primary_context/analysis"

    def __post_init__(self) -> None:
        if (self.state == "known") != bool(self.source_observation_ids):
            raise ValueError("only a corroborated chain carries a source reference")
        if len(self.source_observation_ids) > 1 or len(self.encoded().encode()) > 256:
            raise ValueError("content-source audit exceeds its one-ID/256-byte limit")

    def encoded(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=True, separators=(",", ":"))


def inspect_primary_content_sources(
    repo: ContentSourceReader, *, bill_snapshot_id: str, change_snapshot_id: str
) -> ContentSourceAudit:
    try:
        return _inspect(repo, bill_snapshot_id, change_snapshot_id)
    except ValueError:
        return ContentSourceAudit("invalid", "invalid_retained_metadata")


def _inspect(
    repo: ContentSourceReader,
    bill_snapshot_id: str,
    change_snapshot_id: str,
    observation_id: str | None = None,
) -> ContentSourceAudit:
    bill_source = repo.get_content_snapshot(bill_snapshot_id)
    change_source = repo.get_content_snapshot(change_snapshot_id)
    if bill_source is None or change_source is None:
        return ContentSourceAudit("unknown", "context_missing")
    if (
        bill_source.snapshot_id,
        bill_source.kind,
        change_source.snapshot_id,
        change_source.kind,
    ) != (bill_snapshot_id, "bill", change_snapshot_id, "change"):
        return ContentSourceAudit("invalid", "context_identity")
    bill = Bill.model_validate_json(bill_source.payload_json)
    change = StatusChange.model_validate_json(change_source.payload_json)
    if (change.term, change.number) != (bill.term, bill.number):
        return ContentSourceAudit("invalid", "context_owner")
    if not bill.has_process or amendments_stage(change.new_stages) is None:
        return ContentSourceAudit("unknown", "outside_initial_scope")
    record = bill.analysis
    if record is None or record.computation_id is None or record.request_sha256 is None:
        return ContentSourceAudit("unknown", "paid_reference_missing")
    paid = repo.get_computation_result(record.computation_id)
    if paid is None:
        return ContentSourceAudit("unknown", "result_missing")
    if paid.result_id != record.computation_id:
        return ContentSourceAudit("invalid", "result_identity")
    if (paid.term, paid.number) != (bill.term, bill.number):
        return ContentSourceAudit("unknown", "foreign_owner")
    if paid.purpose not in ("analysis", "reanalysis") or not isinstance(
        paid.record, AnalysisRecord
    ):
        return ContentSourceAudit("invalid", "result_kind")
    if not primary_record_matches(record, paid):
        return ContentSourceAudit("invalid", "paid_record_mismatch")
    if record.model_dump(exclude=set(PRIMARY_USAGE_FIELDS)) != paid.record.model_dump(
        exclude=set(PRIMARY_USAGE_FIELDS)
    ):
        return ContentSourceAudit("unknown", "nonfresh_projection")
    if paid.input_binding is None or paid.request_sha256 is None:
        return ContentSourceAudit("unknown", "input_binding_missing")
    if (paid.input_binding.term, paid.input_binding.number) != (paid.term, paid.number):
        return ContentSourceAudit("invalid", "input_binding_owner")
    item = repo.get_consumed_primary_input(
        paid.provider, paid.batch_id, paid.custom_id, bill.analysis_generation
    )
    if item is None:
        return ContentSourceAudit("unknown", "consumed_projection_missing")
    if not isinstance(item.meta, BatchItemMeta) or (
        item.term,
        item.number,
        item.batch_id,
        item.custom_id,
        item.call_kind,
    ) != (paid.term, paid.number, paid.batch_id, paid.custom_id, paid.purpose):
        return ContentSourceAudit("invalid", "item_identity")
    meta = item.meta
    receipt = meta.prepared_input_receipt
    if receipt is None or meta.input_binding is None or meta.request_sha256 is None:
        return ContentSourceAudit("unknown", "prepared_receipt_missing")
    if (
        meta.input_binding != paid.input_binding
        or meta.generation != paid.generation
        or meta.request_sha256 != paid.request_sha256
        or receipt.request_sha256 != paid.request_sha256
        or meta.input_chars != receipt.text_chars
        or paid.record.input_chars != receipt.text_chars
    ):
        return ContentSourceAudit("invalid", "request_binding")
    queued_at = meta.queued_at
    if queued_at is None or queued_at.utcoffset() is None:
        return ContentSourceAudit("unknown", "queue_time_unknown")
    if paid.collected_at.utcoffset() is None:
        return ContentSourceAudit("unknown", "collection_time_unknown")
    if queued_at > paid.collected_at:
        return ContentSourceAudit("invalid", "queue_after_collection")
    if receipt.source_selection_id is None:
        return ContentSourceAudit("unknown", "selection_unmapped")
    selected = repo.get_document_selection(receipt.source_selection_id)
    if selected is None:
        return ContentSourceAudit("unknown", "selection_missing")
    if selected.selection_id != receipt.source_selection_id or len(selected.selection.parts) != 1:
        return ContentSourceAudit("invalid", "selection_identity")
    part = selected.selection.parts[0]
    if (
        part.members
        or part.role != receipt.source_part.role
        or part.text_sha256 != receipt.source_part.text_sha256
        or part.text_end is not None
        and part.text_end - part.text_start != receipt.source_part.chars
    ):
        return ContentSourceAudit("invalid", "selection_input")
    if observation_id is None:
        observed = repo.source_observations_for_selection(
            bill.term, bill.number, selected.selection_id
        )
        if len(observed) != 1:
            return ContentSourceAudit(
                "unknown", "observation_ambiguous" if observed else "observation_missing"
            )
        source = observed[0]
    else:
        retained = repo.get_source_observation(observation_id)
        if retained is None:
            return ContentSourceAudit("invalid", "stored_observation_missing")
        source = retained
        if source.observation_id != observation_id:
            return ContentSourceAudit("invalid", "stored_observation_identity")
    if (source.term, source.number, source.purpose, source.selection_id) != (
        bill.term,
        bill.number,
        "analysis",
        selected.selection_id,
    ):
        return ContentSourceAudit("invalid", "observation_identity")
    if source.recorded_at > queued_at:
        return ContentSourceAudit("unknown", "observation_after_queue")
    return ContentSourceAudit("known", "exact_metadata_chain", (source.observation_id,))


def verify_primary_content_source(
    repo: ContentSourceReader,
    *,
    bill_snapshot_id: str,
    change_snapshot_id: str,
    observation_id: str,
) -> ContentSourceAudit:
    try:
        return _inspect(repo, bill_snapshot_id, change_snapshot_id, observation_id)
    except ValueError:
        return ContentSourceAudit("invalid", "invalid_retained_metadata")


class SourceContextPublicationReader(Protocol):
    def publication_by_id(self, publication_id: int) -> Publication | None: ...
    def digest_sources(self, owner_id: int) -> list[Publication]: ...


def source_context_blocked(repo: SourceContextPublicationReader, publication: Publication) -> bool:
    pending = [publication]
    if publication.id is not None:
        stored = repo.publication_by_id(publication.id)
        if stored is not None:
            pending.append(stored)
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current.error == SOURCE_CONTEXT_INVALID:
            return True
        if current.id is not None:
            if current.id in seen:
                continue
            seen.add(current.id)
        plan = current.delivery
        if plan is not None:
            identities = (
                *plan.publication_ids,
                *plan.continuation_ids,
                *plan.sitting_group,
                *plan.digest_parts,
            )
            identities += tuple(item.publication_id for item in plan.update_group)
            if plan.continuation_root is not None:
                identities += (plan.continuation_root,)
            for identity in identities:
                if (
                    identity not in seen
                    and (related := repo.publication_by_id(identity)) is not None
                ):
                    pending.append(related)
        if current.kind is PublicationKind.DIGEST and current.id is not None:
            pending.extend(repo.digest_sources(current.id))
    return False
