import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from lexinform.ports import Clock
from lexinform_web.ingestion.acquisition import GitSnapshotSource, SnapshotRef, acquire_snapshot
from lexinform_web.ingestion.contract import Identity, ImportDocumentV1, ImportedBill
from lexinform_web.ingestion.models import (
    ActiveImport,
    EventRevision,
    ImportGeneration,
    MatterEvent,
    MatterFacts,
    SourceSnapshot,
)
from lexinform_web.ingestion.process import project_in_process
from lexinform_web.ingestion.restore import SnapshotError
from lexinform_web.matters.identities import IdentitySource, SourceKey
from lexinform_web.matters.models import Matter, MatterRelation, SourceIdentity
from lexinform_web.matters.selectors import canonical_matter
from lexinform_web.matters.services import identify, identity_transaction, relate

RELATION_KINDS = {
    "joint": MatterRelation.Kind.JOINT,
    "alternative": MatterRelation.Kind.ALTERNATIVE,
    "continuation": MatterRelation.Kind.CONTINUATION,
}


class StaleActivationError(SnapshotError):
    pass


class ReconciliationRequiredError(SnapshotError):
    pass


class CorpusDropError(SnapshotError):
    pass


# data.md §25.5 step 8: a fall of the visible corpus beyond 20% waits for reconciliation.
MAX_CORPUS_DROP = 0.2


@dataclass(frozen=True)
class Activation:
    status: Literal["activated", "unchanged"]
    generation: ImportGeneration
    absent: tuple[int, ...] = ()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def semantic_hash(bill: ImportedBill) -> str:
    """Hash of what the bill says; check times and outcomes are freshness, not content."""
    return digest(
        bill.model_dump(
            mode="json",
            exclude={"applied_analysis": {"text_checked_at"}, "checks": True},
        )
    )


def reference_of(generation: ImportGeneration | None) -> SnapshotRef | None:
    if generation is None:
        return None
    return SnapshotRef(generation.source_commit, generation.dump_sha256)


def accepted_reference() -> SnapshotRef | None:
    active = ActiveImport.objects.select_related("generation").filter(pk=1).first()
    return reference_of(active.generation if active else None)


def source_key(identity: Identity) -> SourceKey:
    return SourceKey(IdentitySource(identity.source), identity.scope, identity.external_id)


def stored_identity(identity: Identity) -> SourceIdentity:
    key = source_key(identity)
    return SourceIdentity.objects.get(
        source=key.source, scope=key.scope, external_id=key.external_id
    )


def assign_matters(
    document: ImportDocumentV1, clock: Clock
) -> dict[Identity, tuple[SourceIdentity, Matter]]:
    """Give every identity its permanent Matter; a lifecycle chain shares the one it already has."""
    lifecycle = {r.source: r.target for r in document.relations if r.kind == "lifecycle"}
    chains: dict[Identity, list[Identity]] = {}
    for bill in document.bills:
        end = bill.identity
        while end in lifecycle:
            end = lifecycle[end]
        chains.setdefault(end, []).append(bill.identity)
    assigned: dict[Identity, tuple[SourceIdentity, Matter]] = {}
    for end, members in chains.items():
        known = {
            canonical_matter(row.matter_id).pk
            for member in members
            for row in SourceIdentity.objects.filter(
                source=member.source, scope=member.scope, external_id=member.external_id
            )
        }
        if len(known) > 1:
            raise ReconciliationRequiredError("a lifecycle chain spans several matters")
        target = known.pop() if known else identify(source_key(end), clock).pk
        for member in members:
            matter = identify(source_key(member), clock, continues=target)
            assigned[member] = (stored_identity(member), matter)
    return assigned


def primary_bills(
    document: ImportDocumentV1, assigned: dict[Identity, tuple[SourceIdentity, Matter]]
) -> dict[Matter, ImportedBill]:
    """The bill whose facts a matter shows: the end of its chain, analysed ones first."""
    linked = {r.source for r in document.relations if r.kind == "lifecycle"}
    primary: dict[Matter, ImportedBill] = {}
    for bill in sorted(
        (bill for bill in document.bills if bill.identity not in linked),
        key=lambda bill: (bill.applied_analysis is not None, bill.identity.model_dump_json()),
    ):
        primary[assigned[bill.identity][1]] = bill
    return primary


def corpus_dropped(previous: ImportGeneration, document: ImportDocumentV1) -> bool:
    before = int(previous.counts.get("analyzed_candidates", 0))
    return document.coverage.analyzed_candidates < before * (1 - MAX_CORPUS_DROP)


def activate(
    document: ImportDocumentV1,
    *,
    expected: SnapshotRef | None,
    clock: Clock,
    accept_drop: bool = False,
) -> Activation:
    """Write a generation and switch the active pointer in one transaction, if nobody moved it."""
    reference = SnapshotRef(document.origin.source_commit, document.origin.dump_sha256)
    with identity_transaction():
        ActiveImport.objects.get_or_create(pk=1)
        active = (
            ActiveImport.objects.select_for_update(of=("self",))
            .select_related("generation")
            .get(pk=1)
        )
        previous = active.generation
        if previous is not None and reference_of(previous) == reference:
            return Activation("unchanged", previous)
        if reference_of(previous) != expected:
            raise StaleActivationError("the accepted snapshot changed during import")
        now = clock.now()
        generation = ImportGeneration.objects.create(
            source_commit=reference.commit,
            dump_sha256=reference.dump_sha256,
            source_schema=document.origin.source_schema,
            contract_version=document.contract_version,
            created_at=now,
            counts={},
        )
        assigned = assign_matters(document, clock)
        bills = {bill.identity: bill for bill in document.bills}
        for relation in document.relations:
            if relation.kind == "lifecycle":
                continue
            first, second = assigned[relation.source][1], assigned[relation.target][1]
            if first.pk != second.pk:
                relate(
                    first.pk,
                    second.pk,
                    RELATION_KINDS[relation.kind],
                    bills[relation.source].facts.source_link.url,
                )
        snapshots = {
            bill.identity: SourceSnapshot.objects.create(
                generation=generation,
                identity=assigned[bill.identity][0],
                payload=bill.model_dump(mode="json"),
                semantic_hash=semantic_hash(bill),
            )
            for bill in document.bills
        }
        earlier = (
            {
                facts.matter_id: facts
                for facts in MatterFacts.objects.filter(generation=previous).select_related(
                    "snapshot"
                )
            }
            if previous
            else {}
        )
        for matter, bill in primary_bills(document, assigned).items():
            snapshot = snapshots[bill.identity]
            before = earlier.get(matter.pk)
            unchanged = (
                before is not None
                and before.snapshot.identity_id == snapshot.identity_id
                and before.snapshot.semantic_hash == snapshot.semantic_hash
            )
            analysis = bill.applied_analysis
            MatterFacts.objects.create(
                generation=generation,
                matter=matter,
                snapshot=snapshot,
                title=bill.facts.title,
                bot_status=bill.bot_status.value,
                relevant=analysis.relevant if analysis else None,
                score=analysis.score if analysis else None,
                category=analysis.category.value if analysis else "",
                document_date=bill.facts.document_date,
                closure_date=bill.facts.closure_date,
                content_updated_at=before.content_updated_at if before and unchanged else now,
            )
        new_events = revised_events = 0
        for event in document.events:
            identity, matter = assigned[event.identity]
            stored, created = MatterEvent.objects.get_or_create(
                event_key=event.event_key,
                defaults={
                    "matter": matter,
                    "identity": identity,
                    "provenance": event.provenance,
                    "observed_at": event.observed_at,
                    "occurred_at": event.occurred_at,
                },
            )
            content = event.model_dump(mode="json", exclude={"event_key", "identity"})
            latest = stored.revisions.order_by("-generation_id").first()
            if latest is None or latest.content_hash != digest(content):
                EventRevision.objects.create(
                    event=stored,
                    generation=generation,
                    content=content,
                    content_hash=digest(content),
                )
                new_events += created
                revised_events += not created
        absent = (
            tuple(
                SourceSnapshot.objects.filter(generation=previous)
                .exclude(identity__in=[row for row, _ in assigned.values()])
                .order_by("identity_id")
                .values_list("identity_id", flat=True)
            )
            if previous
            else ()
        )
        generation.counts = {
            **document.coverage.model_dump(),
            "matters": len({matter.pk for _, matter in assigned.values()}),
            "new_events": new_events,
            "revised_events": revised_events,
            "absent_identities": len(absent),
        }
        if previous is not None and not accept_drop and corpus_dropped(previous, document):
            raise CorpusDropError("visible corpus fell by more than 20%; reconciliation required")
        generation.save(update_fields=["counts"])
        active.generation = generation
        active.save(update_fields=["generation"])
        return Activation("activated", generation, absent)


def import_state(
    source: GitSnapshotSource,
    *,
    clock: Clock,
    public_channel: str | None = None,
    timeout: float = 30,
    accept_drop: bool = False,
    rebaseline: str | None = None,
) -> Activation | Literal["unchanged", "already_running"]:
    """Fetch, project and activate one snapshot; any failure leaves the active generation as is."""
    previous: list[SnapshotRef | None] = []

    def load() -> SnapshotRef | None:
        previous.append(accepted_reference())
        return previous[0]

    with acquire_snapshot(source, load, rebaseline=rebaseline) as acquisition:
        if acquisition.status != "ready":
            return acquisition.status
        assert acquisition.reference and acquisition.raw is not None, "fetch returns ready bytes"
        document = project_in_process(
            acquisition.raw,
            source_commit=acquisition.reference.commit,
            public_channel=public_channel,
            timeout=timeout,
        )
        return activate(document, expected=previous[0], clock=clock, accept_drop=accept_drop)
