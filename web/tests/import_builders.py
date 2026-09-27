import datetime as dt
from dataclasses import dataclass

from lexinform.models.enums import BillStatus, Category
from lexinform_web.ingestion.acquisition import SnapshotRef
from lexinform_web.ingestion.contract import (
    AppliedAnalysis,
    Coverage,
    Identity,
    IdentityRelation,
    ImportDocumentV1,
    ImportedBill,
    ImportedEvent,
    PublicLink,
    SnapshotOrigin,
    SourceFacts,
)

T0 = dt.datetime(2026, 9, 27, 8, tzinfo=dt.UTC)


@dataclass
class MovingClock:
    at: dt.datetime = T0

    def now(self) -> dt.datetime:
        return self.at


def sejm(number: str) -> Identity:
    return Identity(source="sejm", scope="10", external_id=number)


RCL = Identity(source="rcl", scope="global", external_id="12400301")


def bill(
    identity: Identity,
    title: str = "Projekt ustawy o cudzoziemcach",
    *,
    checked: dt.datetime | None = None,
    status: BillStatus = BillStatus.ANALYZED,
) -> ImportedBill:
    return ImportedBill(
        identity=identity,
        bot_status=status,
        facts=SourceFacts(
            title=title,
            description=None,
            source_link=PublicLink(url=f"https://api.sejm.gov.pl/{identity.external_id}"),
            document_date=dt.date(2026, 9, 1),
            closure_date=None,
            passed=None,
            stages=(),
        ),
        processed=None,
        observed_closure_date=None,
        applied_analysis=AppliedAnalysis(
            relevant=True,
            score=4,
            category=Category.LEGAL_STAY,
            summary="Summary",
            rationale="Rationale",
            key_changes=(),
            affected_groups=(),
            practical_impact="Impact",
            effective_date=None,
            changes_since_previous=(),
            created_at=T0,
            revision=1,
            source_link=None,
            text_sha256=None,
            text_checked_at=checked,
        ),
        awaiting_batch_since=None,
        has_staged_analysis=False,
    )


def event(
    identity: Identity, key: str = "state:10:1:abc", *, withdrawn: bool = False
) -> ImportedEvent:
    return ImportedEvent(
        event_key=key,
        identity=identity,
        observed_at=T0,
        stages=(),
        closure_detected=False,
        passed=None,
        content_changed=False,
        withdrawn=withdrawn,
        discontinued=False,
        consultation_opened=False,
    )


def document(
    commit: str,
    *bills: ImportedBill,
    relations: tuple[IdentityRelation, ...] = (),
    events: tuple[ImportedEvent, ...] = (),
) -> ImportDocumentV1:
    return ImportDocumentV1(
        origin=SnapshotOrigin(source_commit=commit * 40, dump_sha256=commit * 64, source_schema=34),
        bills=bills,
        relations=relations,
        events=events,
        coverage=Coverage(
            total=len(bills),
            with_applied_analysis=sum(b.applied_analysis is not None for b in bills),
            analyzed_candidates=sum(
                b.bot_status == BillStatus.ANALYZED and b.applied_analysis is not None
                for b in bills
            ),
        ),
    )


def ref(commit: str) -> SnapshotRef:
    return SnapshotRef(commit * 40, commit * 64)
