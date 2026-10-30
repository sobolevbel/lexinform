"""Retain and apply frozen amendment corrections independently of provider collection."""

import logging
from datetime import datetime

from lexinform.models import (
    AmendmentRevisionApplicationWork,
    AmendmentRevisionTarget,
    AmendmentsRecord,
    Bill,
    ContentSnapshot,
    DeliveryPlan,
    LlmBatchItem,
    Publication,
    PublicationKind,
    PublicationStatus,
    ResultApplicationTarget,
    StatusChange,
)
from lexinform.ports import BillRepository, Clock
from lexinform.services.result_applications import record_secondary_application

log = logging.getLogger(__name__)


class AmendmentRevisionApplications:
    def __init__(self, repo: BillRepository, clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    def pending(self, publication_id: int) -> datetime | None:
        work = self._repo.list_revision_application_work(publication_id=publication_id)
        return min((item.created_at for item in work), default=None)

    def freeze(
        self, bill: Bill, target: AmendmentRevisionTarget, *, amendment_key: str
    ) -> tuple[AmendmentRevisionTarget, tuple[ContentSnapshot, ...]]:
        original = self._repo.publication_by_id(target.publication_id)
        if (
            not bill.has_process
            or original is None
            or original.kind is not PublicationKind.STATUS_UPDATE
            or original.status is not PublicationStatus.SENT
            or (original.term, original.number, original.channel_id, original.message_id)
            != (bill.term, bill.number, target.channel_id, target.delivery.edit_message_id)
        ):
            raise ValueError("Historical amendment corrections require the original Sejm target")
        frozen = target.model_copy(
            update={"delivery": target.delivery.model_copy(update={"amendment_key": amendment_key})}
        )
        snapshot, snapshots = self._snapshots(frozen)
        if not snapshot.has_process or (snapshot.term, snapshot.number) != (bill.term, bill.number):
            raise ValueError("The frozen amendment snapshot belongs to another owner")
        return frozen, snapshots

    @staticmethod
    def _snapshots(
        target: AmendmentRevisionTarget,
    ) -> tuple[Bill, tuple[ContentSnapshot, ...]]:
        plan = target.delivery
        bill = Bill.model_validate_json(plan.bill_json)
        template = plan.model_copy(update={"bill_json": "", "change_json": "", "rendered_text": ""})
        return bill, (
            ContentSnapshot(kind="bill", payload_json=plan.bill_json),
            ContentSnapshot(kind="change", payload_json=plan.change_json),
            ContentSnapshot(kind="plan", payload_json=template.model_dump_json()),
        )

    def retain(
        self,
        item: LlmBatchItem,
        target: AmendmentRevisionTarget,
        record: AmendmentsRecord,
        *,
        now: datetime,
    ) -> None:
        try:
            bill, snapshots = self._snapshots(target)
        except ValueError:
            self._unsupported(item, "invalid_context")
            return
        if not bill.has_process or (bill.term, bill.number) != (item.term, item.number):
            self._unsupported(item, "unsupported_owner")
            return
        original = self._repo.publication_by_id(target.publication_id)
        if original is None or (original.term, original.number, original.channel_id) != (
            item.term,
            item.number,
            target.channel_id,
        ):
            self._unsupported(item, "target_owner_changed")
            return
        try:
            work = AmendmentRevisionApplicationWork(
                term=item.term,
                number=item.number,
                result_id=record.computation_id or "",
                request_key=item.custom_id,
                request_sha256=record.request_sha256 or "",
                original_publication_id=target.publication_id,
                channel_id=target.channel_id,
                message_id=target.delivery.edit_message_id or 0,
                source_url=target.source_url,
                bill_snapshot_id=snapshots[0].snapshot_id,
                change_snapshot_id=snapshots[1].snapshot_id,
                plan_snapshot_id=snapshots[2].snapshot_id,
                created_at=now,
                next_at=now,
                state="waiting"
                if original.status is PublicationStatus.SENT
                and original.message_id == target.delivery.edit_message_id
                else "blocked",
                problem=""
                if original.status is PublicationStatus.SENT
                and original.message_id == target.delivery.edit_message_id
                else "target_changed",
            )
        except ValueError:
            self._unsupported(item, "invalid_context")
            return
        for snapshot in snapshots:
            self._repo.save_content_snapshot(snapshot)
        try:
            self._repo.save_revision_application_work(work)
        except ValueError:
            self._unsupported(item, "invalid_context")
            return
        self._repo.save_revision_application_handoff(
            item.batch_id, item.custom_id, work_key=work.key
        )

    def _unsupported(self, item: LlmBatchItem, problem: str) -> None:
        self._repo.save_revision_application_handoff(item.batch_id, item.custom_id, problem=problem)
        log.warning("%s: retained amendment correction is unsupported (%s)", item.number, problem)

    def drain(self) -> None:
        for work in self._repo.list_revision_application_work(due=self._clock.now()):
            self._apply(work)

    def _apply(self, offered: AmendmentRevisionApplicationWork) -> None:
        now = self._clock.now()
        with self._repo.atomic():
            work = self._repo.get_revision_application_work(offered.key)
            if work is None or work.state != "waiting" or work.next_at > now:
                return
            original = self._repo.publication_by_id(work.original_publication_id)
            if (
                original is None
                or original.status is not PublicationStatus.SENT
                or original.message_id != work.message_id
            ):
                self._block(work, "target_changed")
                return
            ref = f"amendments/{work.original_publication_id}/{work.request_key}"
            if (
                self._repo.get_publication(
                    work.term,
                    work.number,
                    PublicationKind.AMENDMENTS_REVISION,
                    work.channel_id,
                    ref=ref,
                )
                is not None
            ):
                self._block(work, "existing_revision")
                return
            result = self._repo.get_computation_result(work.result_id)
            assert result is not None and isinstance(result.record, AmendmentsRecord), (
                "the shared work reader validates the retained amendments result"
            )
            bill = self._repo.get_content_snapshot(work.bill_snapshot_id)
            change = self._repo.get_content_snapshot(work.change_snapshot_id)
            plan = self._repo.get_content_snapshot(work.plan_snapshot_id)
            assert bill is not None and change is not None and plan is not None, (
                "the shared work reader validates every frozen snapshot reference"
            )
            revised = StatusChange.model_validate_json(change.payload_json).model_copy(
                update={"amendments": result.record}
            )
            delivery = DeliveryPlan.model_validate_json(plan.payload_json).model_copy(
                update={"bill_json": bill.payload_json, "change_json": revised.model_dump_json()}
            )
            publication_id = self._repo.create_publication(
                Publication(
                    term=work.term,
                    number=work.number,
                    kind=PublicationKind.AMENDMENTS_REVISION,
                    status=PublicationStatus.QUEUED,
                    channel_id=work.channel_id,
                    ref=ref,
                    created_at=now,
                    delivery=delivery,
                )
            )
            record_secondary_application(
                self._repo,
                work.term,
                work.number,
                work.result_id,
                target=ResultApplicationTarget(kind="publication", ref=str(publication_id)),
                now=now,
            )
            self._repo.save_revision_application_work(
                work.model_copy(
                    update={
                        "state": "complete",
                        "revision_publication_id": publication_id,
                        "attempts": work.attempts + 1,
                    }
                )
            )

    def _block(self, work: AmendmentRevisionApplicationWork, problem: str) -> None:
        self._repo.save_revision_application_work(
            work.model_copy(
                update={"state": "blocked", "problem": problem, "attempts": work.attempts + 1}
            )
        )
