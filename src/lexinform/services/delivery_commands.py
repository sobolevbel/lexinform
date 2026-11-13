"""Inspect and resolve persisted Telegram deliveries."""

from collections.abc import Callable

from lexinform.content_versions import is_grouped_stage_revision_slot
from lexinform.models import (
    SILENCED_BY_OPERATOR,
    BillStatus,
    Command,
    CommandOutcome,
    CommandWork,
    DeliveryResolution,
    IncomingCommand,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    card_edit_owner_id,
    failed_card_part_resolution_allowed,
)
from lexinform.ports import BillRepository, Clock
from lexinform.services.content_inspection import inspect_content, inspect_content_page
from lexinform.services.digest import DigestService
from lexinform.services.publications import complete_delivery, source_context_blocked


class DeliveryCommands:
    def __init__(
        self,
        repo: BillRepository,
        clock: Clock,
        channel_id: str,
        digest: DigestService | None,
        source_context_retry: Callable[[DeliveryResolution], bool] | None,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._channel_id = channel_id
        self._digest = digest
        self._source_context_retry = source_context_retry

    def execute(
        self, command: Command, incoming: IncomingCommand, *, publish: bool
    ) -> CommandOutcome:
        assert command.delivery_id is not None
        publication = self._repo.publication_by_id(command.delivery_id)
        if publication is None or (
            publication.channel_id != self._channel_id
            and not (self._digest is not None and self._digest.owns_channel(publication.channel_id))
        ):
            return CommandOutcome(
                status=OutcomeStatus.NOT_FOUND, note="publication not found in this channel"
            )
        if command.delivery_action == "history":
            return CommandOutcome(
                status=OutcomeStatus.DELIVERY,
                content_history_page=inspect_content_page(
                    self._repo, publication, page=command.delivery_history_page
                ),
            )
        owner_id = self._repo.digest_owner(command.delivery_id)
        if owner_id is not None and command.delivery_action != "show":
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                delivery=publication,
                note=f"resolve physical digest part {owner_id}; this row is an included event",
            )
        action = command.delivery_action
        if action != "show" and is_grouped_stage_revision_slot(publication):
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                delivery=publication,
                note="grouped edit is inspectable; delivery resolution is disabled",
            )
        if action != "show" and source_context_blocked(self._repo, publication):
            if action == "retry" and publish and self._source_context_retry is not None:
                resolution = DeliveryResolution(
                    publication_id=command.delivery_id,
                    publication_ids=(command.delivery_id,),
                    term=publication.term,
                    number=publication.number,
                    kind=publication.kind,
                    channel_id=publication.channel_id,
                    action="retry",
                    command=incoming,
                    resolved_at=self._clock.now(),
                    reason=command.delivery_reason,
                )
                if self._source_context_retry(resolution):
                    publication = self._repo.publication_by_id(command.delivery_id)
                    return CommandOutcome(
                        status=OutcomeStatus.DELIVERY,
                        delivery=publication,
                        delivery_history=tuple(
                            self._repo.delivery_resolutions(command.delivery_id)
                        ),
                        content_history=inspect_content(self._repo, publication)
                        if publication
                        else None,
                        note="source context validated; queued for delivery",
                        work=CommandWork(
                            kind="delivery",
                            term=resolution.term,
                            number=resolution.number,
                            publication_id=resolution.publication_id,
                        ),
                    )
            return CommandOutcome(
                status=OutcomeStatus.ERROR,
                delivery=publication,
                note="source context is invalid; delivery remains blocked",
            )
        if action != "show":
            if not publish:
                return CommandOutcome(
                    status=OutcomeStatus.ERROR, note="publishing is off in this run"
                )
            exhausted_digest_retry = (
                action == "retry"
                and publication.kind is PublicationKind.DIGEST
                and publication.status is PublicationStatus.FAILED
                and publication.attempts >= 3
            )
            failed_card_part = self._failed_card_part_resolution_allowed(publication, action)
            if (
                publication.status is not PublicationStatus.UNKNOWN
                and not exhausted_digest_retry
                and not failed_card_part
            ):
                return CommandOutcome(
                    status=OutcomeStatus.ERROR,
                    delivery=publication,
                    note=f"only unknown deliveries can be resolved; status: {publication.status}",
                )
            if (
                action == "confirm"
                and publication.delivery is not None
                and publication.delivery.edit_message_id is not None
                and command.delivery_message_id != publication.delivery.edit_message_id
            ):
                return CommandOutcome(
                    status=OutcomeStatus.ERROR,
                    delivery=publication,
                    note=f"confirm the saved edit target {publication.delivery.edit_message_id}",
                )
            if action == "retry":
                if publication.delivery is None or not publication.delivery.replayable(
                    publication.kind
                ):
                    return CommandOutcome(
                        status=OutcomeStatus.ERROR,
                        delivery=publication,
                        note="no replayable saved payload; retry refused",
                    )
                bill = self._repo.get(publication.term, publication.number)
                if bill is None and publication.kind is not PublicationKind.DIGEST:
                    return CommandOutcome(
                        status=OutcomeStatus.ERROR,
                        delivery=publication,
                        note="bill no longer exists; retry refused",
                    )
                if publication.kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL) and (
                    bill is not None
                    and (
                        bill.status is BillStatus.LINKED or bill.last_error == SILENCED_BY_OPERATOR
                    )
                ):
                    return CommandOutcome(
                        status=OutcomeStatus.ERROR,
                        delivery=publication,
                        note="card is linked or silenced; resolve that state before retrying",
                    )
            resolution = DeliveryResolution(
                publication_id=command.delivery_id,
                publication_ids=publication.delivery.publication_ids
                if publication.delivery is not None and publication.delivery.publication_ids
                else (command.delivery_id,),
                term=publication.term,
                number=publication.number,
                kind=publication.kind,
                channel_id=publication.channel_id,
                action=action,
                command=incoming,
                resolved_at=self._clock.now(),
                message_id=command.delivery_message_id,
                reason=command.delivery_reason,
            )
            with self._repo.atomic():
                if not self._repo.resolve_delivery(resolution):
                    return CommandOutcome(
                        status=OutcomeStatus.ERROR,
                        note="delivery already resolved; inspect it again",
                    )
                if action == "confirm" and publication.delivery is not None:
                    assert resolution.message_id is not None
                    identities = publication.delivery.publication_ids or (command.delivery_id,)
                    for identity in identities:
                        member = self._repo.publication_by_id(identity)
                        assert member is not None
                        complete_delivery(
                            self._repo,
                            member,
                            message_id=resolution.message_id,
                            sent_at=resolution.resolved_at,
                        )
            publication = self._repo.publication_by_id(command.delivery_id)
        return CommandOutcome(
            status=OutcomeStatus.DELIVERY,
            delivery=publication,
            work=CommandWork(
                kind="delivery",
                term=publication.term,
                number=publication.number,
                publication_id=publication.id,
            )
            if action == "retry" and publication
            else None,
            digest_owner=self._repo.publication_by_id(owner_id) if owner_id is not None else None,
            digest_members=tuple(
                member.id
                for member in self._repo.digest_sources(command.delivery_id)
                if member.id is not None
            ),
            delivery_history=tuple(self._repo.delivery_resolutions(command.delivery_id)),
            content_history=inspect_content(self._repo, publication)
            if publication is not None
            else None,
            note="queued for the next publishing/tracking phase" if action == "retry" else "",
        )

    def _failed_card_part_resolution_allowed(self, publication: Publication, action: str) -> bool:
        plan = publication.delivery
        root = (
            self._repo.publication_by_id(plan.continuation_root)
            if plan is not None and plan.continuation_root is not None
            else publication
        )
        owner_id = card_edit_owner_id(root) if root is not None else None
        owner = self._repo.publication_by_id(owner_id) if owner_id is not None else None
        return failed_card_part_resolution_allowed(publication, root, owner, action)
