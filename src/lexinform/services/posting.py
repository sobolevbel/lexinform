"""Replies under a bill's card, recorded as `pending` before they are sent.

A failure is recorded on the same row and retried on later runs up to `max_attempts`; an outage
(`ServiceUnavailableError`) is recorded and re-raised so the phase stops.

A status change made of service stages only (see `models.has_news`) is *held*: its row gets a
`skipped` update publication and nothing is sent; the next update of the bill lists the held
stages before its own and releases them (their rows become `sent` with its message id).
"""

import logging
from collections.abc import Callable
from datetime import date, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from lexinform.attention import assess_attention
from lexinform.delivery_schedule import daily_schedule, sitting_is_close
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    AgendaItem,
    Bill,
    BillStatus,
    DeliveryPlan,
    ParticipationSnapshot,
    Phase,
    PrintInfo,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReadingOutcome,
    SittingNews,
    Stage,
    StatusChange,
    UpdateNews,
    UpdateSnapshot,
    flatten_stages,
    is_substantive,
    participation_view,
    reading_evidence,
    stage_keys,
    update_event,
    window_closes_within,
)
from lexinform.ports import BillRepository, Clock, Publisher, SejmGateway, StateCheckpoint
from lexinform.services.message_parts import continuation_ready, freeze_parts, send_continuations
from lexinform.services.publications import (
    card_revision_owner,
    open_card_edit_ids,
    send_publication,
    send_publications,
)
from lexinform.services.sources import fetch_print
from lexinform.services.tracking_result import TrackingResult
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length
from lexinform.usefulness import digest_reason

log = logging.getLogger(__name__)

Send = Callable[[int | None], int]
"""Sends one post under an optional reply-to message and answers with its message id."""

UPDATE_ANALYSIS_WAIT = timedelta(hours=1)
ROUNDUP_EVENTS = frozenset(
    {
        "rcl_stage",
        "rcl_legal_commission",
        "referral",
        "referrals",
        "committee_report",
        "subcommittee_report",
        "signed",
    }
)
FROZEN_REPLIES = frozenset(
    {
        PublicationKind.STATUS_UPDATE,
        PublicationKind.ACT_PUBLISHED,
        PublicationKind.CONSULTATION_RESULTS,
        PublicationKind.IN_FORCE,
        PublicationKind.CONSULTATION_DEADLINE,
        PublicationKind.DECISION_DEADLINE,
        PublicationKind.HEARING_DEADLINE,
    }
)


def routine_update(bill: Bill, change: StatusChange, plan: DeliveryPlan) -> bool:
    return (
        update_event(change, bill) in ROUNDUP_EVENTS
        and not (
            change.content_changed
            or change.consultation_opened
            or change.closure_detected
            or change.decision_changed
            or change.withdrawn
            or change.discontinued
            or change.amendments
            or change.supplements
        )
        and not plan.with_act
        and plan.edit_message_id is None
        and all(
            not is_substantive(stage)
            or stage.stage_type in {"RclStage", "Referral", "CommitteeReport", "PresidentSignature"}
            for stage in flatten_stages(change.new_stages)
        )
    )


class Told(StrEnum):
    """Disabled delivery queues news; only an explicit editorial hold suppresses it."""

    SENT = "sent"
    FAILED = "failed"
    QUEUED = "queued"


class Poster:
    """Sends the replies of the tracking phase and keeps the `publications` bookkeeping."""

    def __init__(
        self,
        repo: BillRepository,
        publisher: Publisher,
        clock: Clock,
        *,
        gateway: SejmGateway,
        channel_id: str,
        max_attempts: int,
        attention_mode: str = "shadow",
        urgent_days: int = 3,
        max_group: int = 5,
        daily_roundup: bool = False,
        daily_hour: int = 18,
        delivery_attempts: set[int] | None = None,
        consultation_reviews_enabled: bool = False,
        commission_reviews_enabled: bool = False,
        checkpoint: StateCheckpoint | None = None,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._gateway = gateway
        self._channel_id = channel_id
        self._max_attempts = max_attempts
        self._attention_mode = attention_mode
        self._urgent_days = urgent_days
        self._max_group = max_group
        self._daily_roundup = daily_roundup
        self._daily_hour = daily_hour
        self._attention_metrics: dict[str, int] = {}
        self._part_errors: list[str] = []
        self._counted: set[int] = set()
        self._told_windows: dict[tuple[int, str, str, date], tuple[int, int]] = {}
        self._attempted = delivery_attempts if delivery_attempts is not None else set[int]()
        self._consultation_reviews_enabled = consultation_reviews_enabled
        self._commission_reviews_enabled = commission_reviews_enabled
        self._checkpoint = checkpoint
        self._sittings: dict[int, Publication] = {}
        self._updates: dict[int, Publication] = {}

    def start_run(self, *, keep_attempts: bool = False) -> None:
        self._attention_metrics.clear()
        self._part_errors.clear()
        self._counted.clear()
        self._told_windows.clear()
        if not keep_attempts:
            self._attempted.clear()
        self._sittings.clear()
        self._updates.clear()
        for pub in self._repo.list_digest_candidates(
            self._channel_id, eligible_before=self._clock.now() + timedelta(milliseconds=1)
        ):
            assert pub.id is not None and pub.delivery is not None
            current = self._repo.get(pub.term, pub.number)
            priority = current.editorial.priority if current is not None else None
            override = (
                priority is not None
                and priority.expires_at > self._clock.now()
                and (priority.urgent or priority.important)
            )
            if self._attention_mode != "on" or override:
                self._repo.replace_unattempted_delivery(
                    pub.id,
                    pub.delivery.model_copy(
                        update={
                            "destination": "direct",
                            "destination_reason": "operator_override"
                            if override
                            else "policy_disabled",
                            "schedule": None,
                        }
                    ),
                )

    def queue_update(self, pub: Publication) -> None:
        assert pub.id is not None and pub.delivery is not None
        if pub.status is PublicationStatus.QUEUED and pub.attempts == 0:
            self._updates[pub.id] = pub

    def flush_updates(self, result: TrackingResult) -> None:
        for deferred in self._repo.list_deferred_updates(self._channel_id):
            self.queue_update(deferred)
        queued = [
            self._refresh_attention(pub)
            if pub.delivery is not None and pub.delivery.policy_version == "attention-v1"
            else pub
            for pub in self._updates.values()
        ]
        if any(
            pub.delivery is not None and pub.delivery.policy_version == "attention-v1"
            for pub in queued
        ):
            queued.sort(
                key=lambda pub: (
                    not (
                        pub.delivery is not None
                        and pub.delivery.attention is not None
                        and pub.delivery.attention.protected
                    ),
                    pub.delivery.attention.deadline or date.max
                    if pub.delivery is not None and pub.delivery.attention is not None
                    else date.max,
                    pub.term,
                    pub.number,
                    pub.id or 0,
                )
            )
        self._updates.clear()
        batch: list[Publication] = []
        news: list[UpdateNews] = []
        today = self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date()
        for candidate in queued:
            assert candidate.id is not None
            pub = self._repo.publication_by_id(candidate.id)
            if pub is None or pub.status is not PublicationStatus.QUEUED or pub.delivery is None:
                continue
            plan = pub.delivery
            if plan.absorbed_by is not None or pub.id in self._attempted:
                continue
            if plan.policy_version == "attention-v1" and self._never_attempted(pub):
                pub = self._refresh_attention(pub)
                assert pub.delivery is not None
                plan = pub.delivery
            protected = (
                plan.attention is not None
                and plan.attention.protected
                and plan.policy_version == "attention-v1"
            )
            if not self.ready(pub):
                continue
            current = self._repo.get(pub.term, pub.number)
            if (
                not protected
                and plan.wait_until is not None
                and self._clock.now() < plan.wait_until
                and current is not None
                and current.status in (BillStatus.BATCH_PENDING, BillStatus.REANALYSIS_READY)
                and (plan.schedule is None or self._clock.now() < plan.schedule.deliver_by)
            ):
                continue
            item = UpdateNews(
                bill=participation_view(
                    Bill.model_validate_json(plan.bill_json), plan.participation
                ),
                change=StatusChange.model_validate_json(plan.change_json),
                card_message_id=plan.reply_to,
            )
            if protected:
                if batch:
                    self._flush_update_batch(batch, news, result, today)
                    batch, news = [], []
                result.count_post(self.status_update(item.bill, item.change))
                continue
            if batch and (
                (plan.policy_version == "attention-v1" and len(batch) >= self._max_group)
                or (
                    batch[0].delivery is not None
                    and batch[0].delivery.policy_version != plan.policy_version
                )
                or not self._publisher.updates_fit([*news, item], today=today)
            ):
                self._flush_update_batch(batch, news, result, today)
                batch, news = [], []
            batch.append(pub)
            news.append(item)
        if batch:
            self._flush_update_batch(batch, news, result, today)

    def _flush_update_batch(
        self, pubs: list[Publication], news: list[UpdateNews], result: TrackingResult, today: date
    ) -> None:
        if len(pubs) == 1:
            item = news[0]
            result.count_post(self.status_update(item.bill, item.change))
            return
        members = tuple(
            UpdateSnapshot(
                publication_id=pub.id,
                bill_json=pub.delivery.bill_json,
                change_json=pub.delivery.change_json,
                card_message_id=pub.delivery.reply_to,
            )
            for pub in pubs
            if pub.id is not None and pub.delivery is not None
        )
        text = self._publisher.render_updates(news, today=today)
        with self._repo.atomic():
            self._repo.tag_update_group(
                tuple(item.publication_id for item in members),
                f"updates/{members[0].publication_id}",
            )
            for pub in pubs:
                assert pub.status_change_id is not None and pub.delivery is not None
                plan = pub.delivery.model_copy(
                    update={
                        "update_group": members,
                        "group_text": text,
                        "today": today,
                        "wait_until": None,
                    }
                )
                assert self._repo.replace_unsent_update_delivery(
                    pub.status_change_id, self._channel_id, plan
                )
        stored = self._repo.publication_by_id(members[0].publication_id)
        assert stored is not None
        self.retry_update_group(stored, result)

    def retry_update_group(self, pub: Publication, result: TrackingResult) -> bool:
        """A shared send cannot be decomposed into independently retried member posts."""
        assert pub.delivery is not None and pub.delivery.update_group
        plan = pub.delivery
        identities = tuple(item.publication_id for item in pub.delivery.update_group)
        if any(identity in self._attempted for identity in identities):
            return False
        members = [self._repo.publication_by_id(identity) for identity in identities]
        if any(
            member is None
            or member.channel_id != self._channel_id
            or member.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED)
            or member.attempts >= self._max_attempts
            or member.delivery is None
            or member.delivery.update_group != pub.delivery.update_group
            or member.delivery.group_text != pub.delivery.group_text
            for member in members
        ):
            return False
        with self._repo.atomic():
            for identity in identities:
                self._repo.mark_publication(
                    identity, PublicationStatus.PENDING, count_attempt=False
                )
        self._attempted.update(identities)
        sent = send_publications(
            self._repo,
            self._clock,
            identities,
            lambda: self._publisher.publish_updates(plan.group_text),
            checkpoint=self._checkpoint,
        )
        self._count_attention(identities, sent)
        result.count_post(sent is not None)
        if sent is not None:
            result.update_events += len(identities) - 1
        return sent is not None

    def queue_sitting(self, pub: Publication) -> None:
        """Keep a sitting for the run's roundup, which `flush_sittings` sends."""
        assert pub.id is not None and pub.delivery is not None, (
            "prepare_message and list_due_deliveries give only stored rows with a delivery plan"
        )
        if pub.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED):
            return
        if pub.attempts >= self._max_attempts or pub.id in self._attempted:
            return
        self._sittings[pub.id] = pub

    def drop_sitting(self, bill: Bill, ref: str) -> None:
        """An announcement not yet sent of a sitting that is gone: skipped, never retried."""
        pub = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.AGENDA, self._channel_id, ref=ref
        )
        if pub is None or pub.id is None:
            return
        if pub.status in (PublicationStatus.QUEUED, PublicationStatus.FAILED):
            if pub.delivery is not None and (
                pub.delivery.sitting_group or pub.delivery.rendered_text
            ):
                return
            self._sittings.pop(pub.id, None)
            self._repo.mark_publication(pub.id, PublicationStatus.SKIPPED, count_attempt=False)

    def flush_sittings(self, result: TrackingResult) -> None:
        """The publisher sizes messages; every included event shares the delivery result."""
        queued = list(self._sittings.values())
        self._sittings.clear()
        batch: list[Publication] = []
        news: list[SittingNews] = []
        for pub in queued:
            assert pub.id is not None and pub.delivery is not None, "queue_sitting keeps these"
            if pub.id in self._attempted:
                continue
            if pub.delivery.sitting_group:
                if batch:
                    self._send_sittings(batch, news, result)
                    batch, news = [], []
                self._retry_sitting_group(pub, result)
                continue
            if pub.delivery.rendered_text:
                if batch:
                    self._send_sittings(batch, news, result)
                    batch, news = [], []
                sent = self.deliver(pub)
                result.count_post(
                    sent,
                    "agenda_cancelled"
                    if pub.kind is PublicationKind.AGENDA_CANCELLED
                    else "agenda_posted",
                )
                continue
            if pub.delivery.policy_version == "attention-v1":
                pub = self._refresh_attention(pub)
                assert pub.delivery is not None
            assert pub.delivery is not None and pub.id is not None
            if (
                pub.kind is PublicationKind.AGENDA
                and pub.delivery.schedule is not None
                and pub.status is PublicationStatus.QUEUED
                and pub.attempts == 0
                and pub.delivery.item_json is not None
                and AgendaItem.model_validate_json(pub.delivery.item_json).has_started(
                    self._clock.now()
                )
            ):
                self._repo.mark_publication(
                    pub.id,
                    PublicationStatus.SKIPPED,
                    error="Unannounced sitting has already started.",
                    count_attempt=False,
                )
                self._attention_metrics["expired"] = self._attention_metrics.get("expired", 0) + 1
                if self._clock.now() > pub.delivery.schedule.deliver_by:
                    self._attention_metrics["daily_overdue"] = (
                        self._attention_metrics.get("daily_overdue", 0) + 1
                    )
                continue
            if not self.ready(pub):
                continue
            assert pub.delivery is not None
            line = SittingNews.of(pub.kind, pub.delivery)
            if (
                pub.delivery.policy_version == "attention-v1"
                and pub.delivery.attention is not None
                and pub.delivery.attention.protected
            ):
                if batch:
                    self._send_sittings(batch, news, result)
                    batch, news = [], []
                self._send_sittings([pub], [line], result)
                continue
            if batch and (
                (pub.delivery.policy_version == "attention-v1" and len(batch) >= self._max_group)
                or (
                    batch[0].delivery is not None
                    and batch[0].delivery.policy_version != pub.delivery.policy_version
                )
                or not self._publisher.sittings_fit([*news, line])
            ):
                self._send_sittings(batch, news, result)
                batch, news = [], []
            batch.append(pub)
            news.append(line)
        if batch:
            self._send_sittings(batch, news, result)

    def _send_sittings(
        self, pubs: list[Publication], news: list[SittingNews], result: TrackingResult
    ) -> None:
        if len(pubs) == 1 and utf16_length(self._publisher.render_sittings(news)) > MESSAGE_LIMIT:
            sent = self.deliver(pubs[0])
            result.count_post(sent, "agenda_cancelled" if news[0].cancelled else "agenda_posted")
            return
        if all(
            pub.delivery is not None and pub.status is PublicationStatus.QUEUED and not pub.attempts
            for pub in pubs
        ):
            group_ids = tuple(pub.id for pub in pubs if pub.id is not None)
            text = self._publisher.render_sittings(news)
            with self._repo.atomic():
                for pub in pubs:
                    assert pub.id is not None and pub.delivery is not None
                    planned = pub.delivery.model_copy(
                        update={"sitting_group": group_ids, "group_text": text}
                    )
                    assert self._repo.replace_unattempted_delivery(pub.id, planned)
            stored = self._repo.publication_by_id(group_ids[0])
            assert stored is not None
            self._retry_sitting_group(stored, result)
            return
        identities: list[int] = []
        with self._repo.atomic():
            for pub in pubs:
                assert pub.id is not None, "queue_sitting keeps stored rows"
                identities.append(pub.id)
                self._repo.mark_publication(pub.id, PublicationStatus.PENDING, count_attempt=False)
        self._attempted.update(identities)
        message_id = send_publications(
            self._repo, self._clock, identities, lambda: self._publisher.publish_sittings(news)
        )
        self._count_attention(tuple(identities), message_id)
        for pub in pubs:
            cancelled = pub.kind is PublicationKind.AGENDA_CANCELLED
            result.count_post(
                message_id is not None, "agenda_cancelled" if cancelled else "agenda_posted"
            )

    def _retry_sitting_group(self, pub: Publication, result: TrackingResult) -> None:
        assert pub.delivery is not None and pub.delivery.sitting_group
        identities = pub.delivery.sitting_group
        if any(identity in self._attempted for identity in identities):
            return
        members = [self._repo.publication_by_id(identity) for identity in identities]
        if any(
            member is None
            or member.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED)
            or member.attempts >= self._max_attempts
            or member.channel_id != self._channel_id
            or member.delivery is None
            or member.delivery.sitting_group != identities
            or member.delivery.group_text != pub.delivery.group_text
            for member in members
        ):
            return
        with self._repo.atomic():
            for identity in identities:
                self._repo.mark_publication(
                    identity, PublicationStatus.PENDING, count_attempt=False
                )
        self._attempted.update(identities)
        sent = send_publications(
            self._repo,
            self._clock,
            identities,
            lambda: self._publisher.publish_prepared(pub),
            checkpoint=self._checkpoint,
        )
        self._count_attention(identities, sent)
        for member in members:
            assert member is not None
            result.count_post(
                sent is not None,
                "agenda_cancelled"
                if member.kind is PublicationKind.AGENDA_CANCELLED
                else "agenda_posted",
            )

    def card(self, bill: Bill) -> Publication | None:
        """The bill's card in this channel: the message every reply is attached to."""
        return self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL, self._channel_id
        )

    def posted(self, bill: Bill, kind: PublicationKind, *, ref: str | None = None) -> bool:
        """True when this post exists and must not be attempted (again)."""
        if (
            kind is PublicationKind.ACT_PUBLISHED
            and self._repo.get_act_update(bill.term, bill.number, self._channel_id) is not None
        ):
            return True
        pub = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
        if pub is None:
            return False
        if pub.id in self._attempted:
            return True
        if pub.status is PublicationStatus.QUEUED:
            return False
        if pub.status is PublicationStatus.FAILED:
            return pub.attempts >= self._max_attempts
        return True

    def sent(self, bill: Bill, kind: PublicationKind, *, ref: str | None = None) -> bool:
        """True when this post actually reached the channel. `posted` answers a different
        question — whether it may be attempted — and a row a crashed run left behind counts as
        posted there while the reader never saw a word of it."""
        pub = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
        return pub is not None and pub.status is PublicationStatus.SENT

    def joint_delivery_exists(self, bill: Bill, kind: PublicationKind, ref: str) -> bool:
        """A joint print owns this event's delivery, including queued or uncertain work."""
        own = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
        retryable = (PublicationStatus.QUEUED, PublicationStatus.FAILED)
        for number in bill.summary.prints_considered_jointly:
            pub = self._repo.get_publication(bill.term, number, kind, self._channel_id, ref=ref)
            if pub is None or pub.status is PublicationStatus.SKIPPED:
                continue
            if own is not None and own.status in retryable and pub.status in retryable:
                assert own.id is not None and pub.id is not None, "stored publications have IDs"
                if own.id < pub.id:
                    continue
            return True
        return False

    def retry_due(self, bill: Bill, kind: PublicationKind, *, ref: str | None = None) -> bool:
        """True when this post was attempted, failed, and still has attempts left."""
        pub = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
        return (
            pub is not None
            and pub.status is PublicationStatus.FAILED
            and pub.attempts < self._max_attempts
        )

    def record(
        self,
        bill: Bill,
        kind: PublicationKind,
        status: PublicationStatus,
        *,
        ref: str | None = None,
    ) -> int:
        """Write the bookkeeping row without sending anything (e.g. `skipped`)."""
        identity = self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=kind,
                status=status,
                channel_id=self._channel_id,
                ref=ref,
                created_at=self._clock.now(),
            )
        )
        if status is PublicationStatus.SKIPPED:
            self._sittings.pop(identity, None)
        return identity

    def rerender_card(
        self, bill: Bill, card: Publication, *, print_info: PrintInfo | None = None
    ) -> bool:
        """Re-render the card in place from `bill` as it stands now; one attempt, a refusal is
        logged and not retried.

        For a thread that gained a number this is the tags; for a term that ended it is the whole
        card, which `CardRefresher` cannot reach — `list_tracked` drops a discontinued row, so
        nothing would ever render the card that says the bill lapsed.
        """
        if card.message_id is None or card.id is None:
            return False
        if open_card_edit_ids(self._repo, card):
            return False
        # A linked print can still carry its RPW analysis while its own batch is pending.
        if print_info is None and bill.has_process:
            print_info = fetch_print(self._gateway, bill)
        try:
            self._publisher.edit_new_bill(bill, print_info, message_id=card.message_id)
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("card of %s not re-rendered: %s: %s", bill.number, type(exc).__name__, exc)
            return False
        self._repo.set_card_digest(card.id, self._publisher.card_digest(bill))
        log.info("card of %s re-rendered in place", bill.number)
        return True

    def record_change(self, change: StatusChange) -> StatusChange | None:
        """Write the change down before anything is said about it; None when an earlier run
        recorded it already (the row is unique per bill and fingerprint), which is what keeps
        the same news from going out twice.

        The change comes back carrying its `id`, so the caller never has to fill it in — which
        is half of a prologue that stood in seven places, each free to forget a piece of it.
        (`record` is the same idea for a publication row and is a different table.)
        """
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return None
        change.id = change_id
        return change

    def tell(
        self,
        bill: Bill,
        change: StatusChange,
        result: TrackingResult,
        *,
        publish: bool,
        with_act: bool = False,
    ) -> Told:
        """A recorded substantive event must have durable delivery work even with publishing off;
        `with_act` tells the act out in the same run in this update, not in a notice of its own."""
        if not publish:
            self.prepare(bill, change, with_act=with_act)
            return Told.QUEUED
        plan = self.prepare(bill, change, with_act=with_act)
        if plan.destination == "digest_only":
            return Told.QUEUED
        if plan.update_group:
            assert change.id is not None
            pub = self._repo.get_update_publication(change.id, self._channel_id)
            assert pub is not None
            sent = self.retry_update_group(pub, result)
            return Told.SENT if sent else Told.FAILED
        routine = routine_update(bill, change, plan)
        if plan.policy_version == "attention-v1" and plan.attention is not None:
            routine = not plan.attention.protected and plan.edit_message_id is None
        if routine and not window_closes_within(
            bill, self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(), 3
        ):
            assert change.id is not None
            pub = self._repo.get_update_publication(change.id, self._channel_id)
            assert pub is not None
            if pub.status is PublicationStatus.QUEUED and pub.attempts == 0:
                self.queue_update(pub)
                return Told.QUEUED
        sent = self.status_update(bill, change, with_act=with_act)
        result.count_post(sent)
        if sent and self.carries_act(change):
            result.acts_published += 1
        return Told.SENT if sent else Told.FAILED

    def carries_act(self, change: StatusChange) -> bool:
        """The update's plan tells the act published in its run."""
        assert change.id is not None, "only a change record_change stored reaches the poster"
        row = self._repo.get_update_publication(change.id, self._channel_id)
        return (
            row is not None
            and row.delivery is not None
            and row.delivery.with_act
            and row.delivery.edit_message_id is None
        )

    def hold(self, bill: Bill, change: StatusChange) -> None:
        """Keep a service-stage change for the next post instead of sending it now."""
        assert change.id is not None, "only a change record_change stored reaches the poster"
        row = self._update_row(bill, change.id, PublicationStatus.SKIPPED)
        if self._attention_mode != "off":
            row.attention = assess_attention(
                bill,
                PublicationKind.STATUS_UPDATE,
                change=change,
                now=self._clock.now(),
                urgent_days=self._urgent_days,
            )
        self._repo.create_publication(row)
        log.info(
            "druk %s: %s held for the next update",
            bill.number,
            "; ".join(st.stage_name for st in change.new_stages) or "closure",
        )

    def status_update(self, bill: Bill, change: StatusChange, *, with_act: bool = False) -> bool:
        """Post a detected change as a reply to the card; True on success.

        Stages held since the previous post are listed first and released against the message
        just sent, not against "the newest update row": a retried update keeps its old row id, so
        a later held row would be the newest one. The saved plan owns its included act notice.
        """
        assert change.id is not None, "only a change record_change stored reaches the poster"
        existing = self._repo.get_update_publication(change.id, self._channel_id)
        if existing is not None:
            if existing.delivery is not None and existing.delivery.absorbed_by is not None:
                return False
            if existing.status is PublicationStatus.SENT:
                return True
            if existing.id in self._attempted:
                return False
            if existing.delivery is not None and existing.delivery.update_group:
                return self.retry_update_group(existing, TrackingResult())
            if existing.status in (
                PublicationStatus.PENDING,
                PublicationStatus.UNKNOWN,
                PublicationStatus.DISMISSED,
            ):
                return False
        delivery = self.prepare(bill, change, with_act=with_act)
        if existing is not None and not self.ready(
            existing.model_copy(update={"delivery": delivery})
        ):
            return False
        if delivery.destination == "digest_only":
            return False
        if delivery.wait_until is not None:
            # Attempted or uncertain sends can never be absorbed into a later analysis update.
            delivery = delivery.model_copy(update={"wait_until": None})
            assert self._repo.replace_unsent_update_delivery(change.id, self._channel_id, delivery)
        pub = self._repo.get_update_publication(change.id, self._channel_id)
        assert pub is not None and pub.id is not None, "prepare stores the update and its plan"
        pub_id = pub.id
        snapshot = Bill.model_validate_json(delivery.bill_json)
        planned_change = StatusChange.model_validate_json(delivery.change_json)
        if (
            delivery.policy_version == "attention-v1"
            and pub.status is PublicationStatus.QUEUED
            and not pub.attempts
        ):
            self._freeze(pub)
        self._attempted.add(pub_id)
        message_id = self._send(
            pub_id,
            snapshot,
            lambda reply_to: self._send_update(snapshot, planned_change, delivery, reply_to),
        )
        return message_id is not None

    def _send_update(
        self, bill: Bill, change: StatusChange, delivery: DeliveryPlan, reply_to: int | None
    ) -> int:
        if delivery.edit_message_id is not None:
            assert delivery.today is not None, "an edit preserves the original message's date"
            self._publisher.edit_status_update(
                bill,
                change,
                message_id=delivery.edit_message_id,
                today=delivery.today,
                with_act=delivery.with_act,
            )
            return delivery.edit_message_id
        return self._publisher.publish_status_update(
            bill, change, delivery.reply_to or reply_to, with_act=delivery.with_act
        ).message_id

    def prepare(self, bill: Bill, change: StatusChange, *, with_act: bool = False) -> DeliveryPlan:
        assert change.id is not None, "only a change record_change stored reaches the poster"
        stored = self._repo.get_update_publication(change.id, self._channel_id)
        if stored is not None and stored.delivery is not None:
            if (
                with_act
                and self._never_attempted(stored)
                and (stored.id is None or self._repo.digest_owner(stored.id) is None)
            ):
                return self._add_act(change.id, stored.delivery, bill)
            return stored.delivery
        completion = self._vote_completion(bill, change) if not with_act else None
        if completion is not None and self._attention_mode != "off":
            assessment = assess_attention(
                bill,
                PublicationKind.STATUS_UPDATE,
                change=change,
                now=self._clock.now(),
                urgent_days=self._urgent_days,
            )
            assessment = assessment.model_copy(
                update={
                    "novelty": "enrichment",
                    "importance": "routine",
                    "explanation": "Vote details enrich a confirmed reader delivery.",
                    "reasons": tuple(
                        reason
                        for reason in assessment.reasons
                        if reason
                        in {
                            "pilny_guard",
                            "operator_priority",
                            "existing_window_guard",
                            "near_reader_deadline",
                        }
                    )
                    + ("enrichment_of_delivered_decision",),
                }
            )
            reason = digest_reason(
                bill,
                PublicationKind.STATUS_UPDATE,
                assessment,
                today=self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(),
                enrichment=True,
            )
            if self._attention_mode == "on" and not reason:
                completion = None
            elif reason:
                completion = completion.model_copy(
                    update={
                        "attention": assessment,
                        "proposed_destination": "digest_only",
                        "destination_reason": reason,
                    }
                )
                if self._attention_mode == "on":
                    completion = completion.model_copy(
                        update={
                            "destination": "digest_only",
                            "digest_eligible_at": self._clock.now(),
                            "edit_message_id": None,
                            "policy_version": "attention-v1",
                            "schedule": None,
                        }
                    )
        if completion is not None:
            with self._repo.atomic():
                self._repo.create_publication(
                    self._update_row(bill, change.id, PublicationStatus.QUEUED)
                )
                self._repo.save_update_delivery(change.id, self._channel_id, completion)
            return completion
        held = self._repo.list_held_status_changes(bill.term, bill.number, self._channel_id)
        deferred = [
            pub
            for pub in self._repo.list_deferred_updates(self._channel_id)
            if pub.term == bill.term
            and pub.number == bill.number
            and pub.status_change_id != change.id
            and pub.delivery is not None
            and not pub.delivery.with_act
            and (
                pub.delivery.schedule is None
                or (pub.delivery.attention is not None and not pub.delivery.attention.protected)
            )
        ]
        stages = [st for earlier in held for st in earlier.new_stages]
        stages.extend(
            stage
            for pub in deferred
            if pub.delivery is not None
            for stage in StatusChange.model_validate_json(pub.delivery.change_json).new_stages
        )
        planned = change.model_copy(update={"new_stages": stages + list(change.new_stages)})
        snapshot = self._repo.get(bill.term, bill.number) or bill
        card = self.card(bill)
        delivery = DeliveryPlan(
            bill_json=snapshot.model_dump_json(),
            attention=assess_attention(
                snapshot,
                PublicationKind.STATUS_UPDATE,
                change=planned,
                now=self._clock.now(),
                with_act=with_act,
                urgent_days=self._urgent_days,
            )
            if self._attention_mode != "off"
            else None,
            policy_version="attention-v1" if self._attention_mode == "on" else "legacy",
            change_json=planned.model_dump_json(),
            held_change_ids=tuple(
                dict.fromkeys(
                    [
                        *(c.id for c in held if c.id is not None),
                        *(
                            pub.status_change_id
                            for pub in deferred
                            if pub.status_change_id is not None
                        ),
                        *(
                            identity
                            for pub in deferred
                            if pub.delivery is not None
                            for identity in pub.delivery.held_change_ids
                        ),
                    ]
                )
            ),
            reply_to=card.message_id if card else None,
            with_act=with_act and snapshot.act is not None,
            today=self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(),
        )
        if (
            snapshot.status is BillStatus.BATCH_PENDING
            and routine_update(snapshot, change, delivery)
            and not window_closes_within(
                snapshot, self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(), 3
            )
        ):
            deadline = min(
                [
                    change.detected_at + UPDATE_ANALYSIS_WAIT,
                    *(
                        pub.delivery.wait_until
                        for pub in deferred
                        if pub.delivery is not None and pub.delivery.wait_until is not None
                    ),
                ]
            )
            delivery = delivery.model_copy(update={"wait_until": deadline})
        delivery = self._schedule(delivery, PublicationKind.STATUS_UPDATE)
        schedules = [
            pub.delivery.schedule
            for pub in deferred
            if pub.delivery is not None and pub.delivery.schedule is not None
        ]
        if schedules and delivery.schedule is not None:
            schedules.append(delivery.schedule)
            earliest = min(schedules, key=lambda schedule: schedule.detected_at)
            delivery = delivery.model_copy(update={"schedule": earliest})
        with self._repo.atomic():
            identity = self._repo.create_publication(
                self._update_row(bill, change.id, PublicationStatus.QUEUED)
            )
            self._repo.save_update_delivery(change.id, self._channel_id, delivery)
            for pub in deferred:
                assert pub.status_change_id is not None and pub.delivery is not None
                assert self._repo.replace_unsent_update_delivery(
                    pub.status_change_id,
                    self._channel_id,
                    pub.delivery.model_copy(update={"absorbed_by": identity}),
                )
        return delivery

    def _vote_completion(self, bill: Bill, change: StatusChange) -> DeliveryPlan | None:
        votes = flatten_stages(change.new_stages)
        if (
            not votes
            or any(
                st.stage_type != "Voting"
                or st.voting is None
                or st.voting.sitting is None
                or st.voting.voting_number is None
                for st in votes
            )
            or change.content_changed
            or change.closure_detected
            or change.withdrawn
            or change.discontinued
            or change.consultation_opened
            or change.amendments
            or change.supplements
        ):
            return None
        identities = {(st.voting.sitting, st.voting.voting_number) for st in votes if st.voting}
        parents = [
            st
            for st in flatten_stages(bill.stages)
            if st.stage_type == "SejmReading"
            and reading_evidence(st).reading is ReadingOutcome.PASSED
            and identities
            <= {
                (child.voting.sitting, child.voting.voting_number)
                for child in flatten_stages(st.children)
                if child.voting is not None
            }
        ]
        if len(parents) != 1:
            return None
        parent_key = stage_keys((parents[0],))[0]
        for earlier in reversed(self._repo.list_status_changes(bill.term, bill.number)):
            if earlier.id is None or earlier.id == change.id:
                continue
            post = self._repo.get_update_publication(earlier.id, self._channel_id)
            if post is None or post.status is not PublicationStatus.SENT or post.message_id is None:
                continue
            delivery = post.delivery
            if delivery is None or not delivery.change_json:
                continue
            original = StatusChange.model_validate_json(delivery.change_json)
            snapshot = Bill.model_validate_json(delivery.bill_json)
            if update_event(original, snapshot) != "passed" or not any(
                stage_keys((st,))[0] == parent_key for st in flatten_stages(original.new_stages)
            ):
                continue
            enriched = original.model_copy(update={"new_stages": [*original.new_stages, *votes]})
            return delivery.model_copy(
                update={
                    "change_json": enriched.model_dump_json(),
                    "rendered_text": "",
                    "edit_message_id": post.message_id,
                    "today": delivery.today
                    or (post.sent_at or post.created_at)
                    .astimezone(ZoneInfo("Europe/Warsaw"))
                    .date(),
                    "held_change_ids": (),
                }
            )
        return None

    @staticmethod
    def _never_attempted(pub: Publication) -> bool:
        return (
            pub.status is PublicationStatus.QUEUED
            and pub.attempts == 0
            and (pub.delivery is None or not pub.delivery.update_group)
            and (pub.delivery is None or not pub.delivery.rendered_text)
        )

    def _add_act(self, change_id: int, delivery: DeliveryPlan, bill: Bill) -> DeliveryPlan:
        """The plan `_detect` froze before the run read the act, now carrying it: nothing has
        been sent from it, so the reader has seen none of the facts it replaces."""
        snapshot = self._repo.get(bill.term, bill.number) or bill
        if snapshot.act is None:
            return delivery
        original = Bill.model_validate_json(delivery.bill_json)
        snapshot = original.model_copy(update={"act": snapshot.act})
        planned = delivery.model_copy(
            update={
                "with_act": True,
                "destination": "direct",
                "destination_reason": "act_evidence",
                "schedule": None,
                "bill_json": snapshot.model_dump_json(),
                "rendered_text": "",
                "attention": assess_attention(
                    snapshot,
                    PublicationKind.STATUS_UPDATE,
                    change=StatusChange.model_validate_json(delivery.change_json),
                    with_act=True,
                    now=self._clock.now(),
                    urgent_days=self._urgent_days,
                )
                if self._attention_mode != "off" or delivery.policy_version == "attention-v1"
                else None,
                "attention_history": delivery.attention_history
                + ((delivery.attention,) if delivery.attention else ()),
            }
        )
        if not self._repo.replace_unsent_update_delivery(change_id, self._channel_id, planned):
            return delivery
        return planned

    def _update_row(self, bill: Bill, change_id: int, status: PublicationStatus) -> Publication:
        return Publication(
            term=bill.term,
            number=bill.number,
            kind=PublicationKind.STATUS_UPDATE,
            status=status,
            channel_id=self._channel_id,
            status_change_id=change_id,
            created_at=self._clock.now(),
        )

    def act_published(self, bill: Bill) -> bool:
        return self._once(bill, PublicationKind.ACT_PUBLISHED)

    def in_force(self, bill: Bill, *, today: date) -> bool:
        return self._once(bill, PublicationKind.IN_FORCE, today=today)

    def consultation_deadline(self, bill: Bill, *, today: date) -> bool:
        return self._once(bill, PublicationKind.CONSULTATION_DEADLINE, today=today)

    def consultation_results(self, bill: Bill) -> bool:
        return self._once(bill, PublicationKind.CONSULTATION_RESULTS)

    def decision_deadline(self, bill: Bill, phase: Phase, *, today: date) -> bool:
        return self._once(
            bill, PublicationKind.DECISION_DEADLINE, ref=phase.key, phase=phase, today=today
        )

    def hearing_deadline(self, bill: Bill, hearing: Stage, *, today: date) -> bool:
        assert hearing.date is not None, "hearings_due keeps only hearings with a date"
        return self._once(
            bill,
            PublicationKind.HEARING_DEADLINE,
            ref=hearing.date.isoformat(),
            hearing=hearing,
            today=today,
        )

    def prepare_message(
        self,
        bill: Bill,
        kind: PublicationKind,
        *,
        ref: str | None = None,
        today: date | None = None,
        item: AgendaItem | None = None,
        moved_from: AgendaItem | None = None,
        phase: Phase | None = None,
        hearing: Stage | None = None,
        still_meets: bool = False,
    ) -> Publication:
        stored = self._repo.get_publication(bill.term, bill.number, kind, self._channel_id, ref=ref)
        if stored is not None and stored.delivery is not None:
            return stored
        card = self.card(bill)
        delivery = DeliveryPlan(
            bill_json=bill.model_dump_json(exclude={"review_work"} if bill.review_work else set()),
            attention=assess_attention(
                bill,
                kind,
                now=self._clock.now(),
                item=item,
                moved_from=moved_from,
                hearing=hearing,
                phase=phase,
                urgent_days=self._urgent_days,
            )
            if self._attention_mode != "off"
            else None,
            policy_version="attention-v1"
            if self._attention_mode == "on"
            or kind
            in (
                PublicationKind.CONSULTATION_REVIEW,
                PublicationKind.COMMISSION_REVIEW,
                PublicationKind.ACT_APPLICATION,
                PublicationKind.EFFECTIVE_RULES,
            )
            else "legacy",
            today=today,
            item_json=item.model_dump_json() if item else None,
            moved_from_json=moved_from.model_dump_json() if moved_from else None,
            phase_json=phase.model_dump_json() if phase else None,
            hearing_json=hearing.model_dump_json() if hearing else None,
            still_meets=still_meets,
            reply_to=card.message_id if card else None,
        )
        delivery = self._schedule(delivery, kind)
        pub = Publication(
            term=bill.term,
            number=bill.number,
            kind=kind,
            status=stored.status if stored else PublicationStatus.QUEUED,
            channel_id=self._channel_id,
            ref=ref,
            delivery=delivery,
            created_at=self._clock.now(),
            attempts=stored.attempts if stored else 0,
        )
        pub.id = self._repo.create_publication(pub)
        return pub

    def _once(
        self,
        bill: Bill,
        kind: PublicationKind,
        *,
        ref: str | None = None,
        today: date | None = None,
        item: AgendaItem | None = None,
        moved_from: AgendaItem | None = None,
        phase: Phase | None = None,
        hearing: Stage | None = None,
        still_meets: bool = False,
    ) -> bool:
        pub = self.prepare_message(
            bill,
            kind,
            ref=ref,
            today=today,
            item=item,
            moved_from=moved_from,
            phase=phase,
            hearing=hearing,
            still_meets=still_meets,
        )
        return self.deliver(pub)

    def delivery_paused(self, pub: Publication) -> bool:
        root = (
            self._repo.publication_by_id(pub.delivery.continuation_root)
            if pub.delivery is not None and pub.delivery.continuation_root is not None
            else pub
        )
        if root is not None and root.kind is PublicationKind.DIGEST and root.id is not None:
            identities = root.delivery.digest_parts if root.delivery else ()
            return any(
                self.delivery_paused(source)
                for identity in identities or (root.id,)
                for source in self._repo.digest_sources(identity)
                if source.status is not PublicationStatus.SENT
            )
        return root is not None and (
            (
                root.kind is PublicationKind.CONSULTATION_REVIEW
                and not self._consultation_reviews_enabled
            )
            or (
                root.kind is PublicationKind.COMMISSION_REVIEW
                and not self._commission_reviews_enabled
            )
        )

    def deliver(self, pub: Publication) -> bool:
        if pub.status is PublicationStatus.SENT:
            return True
        if pub.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED):
            return False
        if self.delivery_paused(pub):
            return False
        root = (
            self._repo.publication_by_id(pub.delivery.continuation_root)
            if pub.delivery is not None and pub.delivery.continuation_root is not None
            else pub
        )
        if root is not None and root.kind in (
            PublicationKind.ACT_APPLICATION,
            PublicationKind.EFFECTIVE_RULES,
        ):
            assert pub.id is not None
            self._repo.mark_publication(pub.id, PublicationStatus.SKIPPED, count_attempt=False)
            return False
        if pub.attempts >= self._max_attempts:
            return False
        assert pub.id is not None and pub.delivery is not None, (
            "prepare_message and list_due_deliveries give only stored rows with a delivery plan"
        )
        if pub.id in self._attempted or pub.delivery.destination == "digest_only":
            return False
        if pub.kind is PublicationKind.MESSAGE_PART:
            ready = continuation_ready(self._repo, pub)
            if ready is None:
                return False
            pub = ready
        assert pub.delivery is not None and pub.id is not None
        if pub.delivery.policy_version == "attention-v1":
            pub = self._refresh_attention(pub)
            assert pub.delivery is not None and pub.id is not None
            assessment = pub.delivery.attention
            if (
                pub.kind is PublicationKind.CONSULTATION_DEADLINE
                and pub.status is PublicationStatus.QUEUED
                and pub.attempts == 0
                and not pub.delivery.rendered_text
            ):
                window = participation_view(
                    Bill.model_validate_json(pub.delivery.bill_json), pub.delivery.participation
                ).consultation
                if window is None or window.end is None:
                    self._attention_metrics["participation_unavailable"] = (
                        self._attention_metrics.get("participation_unavailable", 0) + 1
                    )
                    return False
            if (
                pub.attempts == 0
                and not pub.delivery.rendered_text
                and pub.kind
                in (
                    PublicationKind.CONSULTATION_DEADLINE,
                    PublicationKind.HEARING_DEADLINE,
                    PublicationKind.IN_FORCE,
                )
                and assessment is not None
                and assessment.deadline is not None
            ):
                owner = self._told_windows.get(
                    (pub.term, pub.number, assessment.action, assessment.deadline)
                )
                if owner is not None:
                    plan = pub.delivery.model_copy(update={"absorbed_by": owner[0]})
                    assert self._repo.replace_unattempted_delivery(pub.id, plan)
                    self._repo.mark_publication(
                        pub.id,
                        PublicationStatus.SENT,
                        message_id=owner[1],
                        sent_at=self._clock.now(),
                        count_attempt=False,
                    )
                    self._attention_metrics["absorbed_reminders"] = (
                        self._attention_metrics.get("absorbed_reminders", 0) + 1
                    )
                    return True
            if (
                pub.attempts == 0
                and not pub.delivery.rendered_text
                and pub.kind
                in (PublicationKind.CONSULTATION_DEADLINE, PublicationKind.HEARING_DEADLINE)
                and pub.delivery.attention is not None
                and "expired_window" in pub.delivery.attention.reasons
            ):
                self._repo.mark_publication(pub.id, PublicationStatus.SKIPPED, count_attempt=False)
                self._attention_metrics["expired"] = self._attention_metrics.get("expired", 0) + 1
                return False
            if pub.kind not in FROZEN_REPLIES or (
                pub.status is PublicationStatus.QUEUED and not pub.attempts
            ):
                pub = self._freeze(pub)
        assert pub.id is not None and pub.delivery is not None
        if pub.kind is PublicationKind.AMENDMENTS_REVISION:
            pub = self._freeze(pub)
        assert pub.id is not None and pub.delivery is not None
        self._attempted.add(pub.id)
        plan = pub.delivery
        bill = Bill.model_validate_json(plan.bill_json)
        if plan.rendered_text:
            return (
                self._send(pub.id, bill, lambda _: self._publisher.publish_prepared(pub).message_id)
                is not None
            )
        return self._send(pub.id, bill, lambda _: self._dispatch(pub.kind, bill, plan)) is not None

    def _freeze(self, pub: Publication, *, keep_today: bool = False) -> Publication:
        assert pub.delivery is not None and pub.id is not None
        if pub.delivery.rendered_text:
            return pub
        if (
            pub.kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED)
            and pub.status is PublicationStatus.QUEUED
            and not pub.attempts
        ):
            text = self._publisher.render_sittings((SittingNews.of(pub.kind, pub.delivery),))
            if utf16_length(text) > MESSAGE_LIMIT:
                return self._freeze_sitting(pub, text)
        today = (
            pub.delivery.today
            if pub.delivery.edit_message_id is not None or keep_today
            else self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date()
        )
        if (
            pub.kind is PublicationKind.STATUS_UPDATE
            and pub.delivery.edit_message_id is not None
            and today is None
        ):
            raise ValueError("The saved update edit lacks its original date")
        if pub.kind in FROZEN_REPLIES and pub.kind not in (
            PublicationKind.STATUS_UPDATE,
            PublicationKind.ACT_PUBLISHED,
            PublicationKind.CONSULTATION_RESULTS,
        ):
            if today is None:
                raise ValueError("The saved reminder lacks its date")
            if pub.kind is PublicationKind.DECISION_DEADLINE and pub.delivery.phase_json is None:
                raise ValueError("The saved decision reminder lacks its phase")
            if pub.kind is PublicationKind.HEARING_DEADLINE and pub.delivery.hearing_json is None:
                raise ValueError("The saved hearing reminder lacks its hearing")
        plan = pub.delivery.model_copy(update={"today": today})
        pub = pub.model_copy(update={"delivery": plan})
        return freeze_parts(
            self._repo, self._clock, self._publisher, pub, self._publisher.render_delivery(pub)
        )

    def _freeze_sitting(self, pub: Publication, text: str) -> Publication:
        assert pub.delivery is not None, "only a saved sitting reaches the physical-part producer"
        return freeze_parts(
            self._repo,
            self._clock,
            self._publisher,
            pub.model_copy(update={"delivery": pub.delivery.model_copy(update={"reply_to": None})}),
            text,
        )

    def _schedule(self, plan: DeliveryPlan, kind: PublicationKind) -> DeliveryPlan:
        bill = Bill.model_validate_json(plan.bill_json)
        phase = Phase.model_validate_json(plan.phase_json) if plan.phase_json else None
        reason = digest_reason(
            bill,
            kind,
            plan.attention,
            phase=phase,
            change=StatusChange.model_validate_json(plan.change_json) if plan.change_json else None,
            today=self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(),
        )
        if reason and self._attention_mode != "off":
            plan = plan.model_copy(
                update={"proposed_destination": "digest_only", "destination_reason": reason}
            )
            if self._attention_mode == "on":
                return plan.model_copy(
                    update={
                        "destination": "digest_only",
                        "digest_eligible_at": self._clock.now(),
                        "schedule": None,
                    }
                )
        if not self._daily_roundup or self._attention_mode != "on" or plan.edit_message_id:
            return plan
        schedule = daily_schedule(
            plan.attention,
            kind,
            now=self._clock.now(),
            release_hour=self._daily_hour,
            urgent_days=self._urgent_days,
        )
        return plan.model_copy(update={"schedule": schedule})

    def ready(self, pub: Publication) -> bool:
        plan = pub.delivery
        if plan is not None and plan.destination == "digest_only":
            return False
        if (
            plan is None
            or plan.schedule is None
            or pub.status is not PublicationStatus.QUEUED
            or pub.attempts
            or plan.publication_ids
            or plan.rendered_text
            or not self._daily_roundup
            or self._attention_mode != "on"
        ):
            return True
        assessment = plan.attention
        if assessment is None or assessment.protected:
            return True
        if plan.schedule.route == "daily_sittings" and sitting_is_close(
            assessment, now=self._clock.now(), urgent_days=self._urgent_days
        ):
            return True
        return self._clock.now() >= min(plan.schedule.not_before, plan.schedule.deliver_by)

    def _refresh_attention(self, pub: Publication) -> Publication:
        assert pub.delivery is not None and pub.id is not None
        if (
            pub.status is not PublicationStatus.QUEUED
            or pub.attempts
            or pub.delivery.publication_ids
            or pub.delivery.rendered_text
            or pub.delivery.edit_message_id is not None
        ):
            return pub
        plan = pub.delivery
        bill = Bill.model_validate_json(plan.bill_json)
        original_editorial = bill.editorial
        participation = plan.participation
        current = self._repo.get(pub.term, pub.number)
        if current is not None:
            bill = bill.model_copy(update={"editorial": current.editorial})
            if participation_view(bill, participation).consultation != current.consultation:
                participation = ParticipationSnapshot(
                    consultation=current.consultation, checked_at=self._clock.now()
                )
        assessment = assess_attention(
            participation_view(bill, participation),
            pub.kind,
            now=self._clock.now(),
            detected_at=plan.attention.detected_at if plan.attention else pub.created_at,
            change=StatusChange.model_validate_json(plan.change_json) if plan.change_json else None,
            item=AgendaItem.model_validate_json(plan.item_json) if plan.item_json else None,
            moved_from=AgendaItem.model_validate_json(plan.moved_from_json)
            if plan.moved_from_json
            else None,
            hearing=Stage.model_validate_json(plan.hearing_json) if plan.hearing_json else None,
            with_act=plan.with_act,
            phase=Phase.model_validate_json(plan.phase_json) if plan.phase_json else None,
            urgent_days=self._urgent_days,
        )
        previous = plan.attention
        if (
            previous is not None
            and previous.model_dump(exclude={"assessed_at"})
            == assessment.model_dump(exclude={"assessed_at"})
            and participation == plan.participation
            and original_editorial == bill.editorial
        ):
            return pub
        plan = plan.model_copy(
            update={
                "attention": assessment,
                "attention_history": plan.attention_history + ((previous,) if previous else ()),
                "rendered_text": "",
                "today": self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(),
                "bill_json": bill.model_dump_json(),
                "participation": participation,
            }
        )
        if self._repo.replace_unattempted_delivery(pub.id, plan):
            return pub.model_copy(update={"delivery": plan})
        return pub

    def _dispatch(self, kind: PublicationKind, bill: Bill, plan: DeliveryPlan) -> int:
        reply = plan.reply_to
        if kind is PublicationKind.STATUS_UPDATE:
            return self._send_update(
                bill, StatusChange.model_validate_json(plan.change_json), plan, reply
            )
        if kind is PublicationKind.ACT_PUBLISHED:
            return self._publisher.publish_act_published(bill, reply).message_id
        if kind is PublicationKind.CONSULTATION_RESULTS:
            return self._publisher.publish_consultation_results(bill, reply).message_id
        assert kind not in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED), (
            "sittings go out together through queue_sitting and flush_sittings"
        )
        assert plan.today is not None, f"a {kind} reminder is prepared with the day it is for"
        if kind is PublicationKind.IN_FORCE:
            return self._publisher.publish_in_force(bill, reply, today=plan.today).message_id
        if kind is PublicationKind.CONSULTATION_DEADLINE:
            return self._publisher.publish_consultation_deadline(
                bill, reply, today=plan.today
            ).message_id
        if kind is PublicationKind.DECISION_DEADLINE:
            assert plan.phase_json is not None, "a decision reminder is prepared with its phase"
            phase = Phase.model_validate_json(plan.phase_json)
            return self._publisher.publish_decision_deadline(
                bill, phase, reply, today=plan.today
            ).message_id
        assert kind is PublicationKind.HEARING_DEADLINE and plan.hearing_json is not None, (
            f"{kind} is not dispatched above, or a hearing reminder lacks its hearing"
        )
        hearing = Stage.model_validate_json(plan.hearing_json)
        return self._publisher.publish_hearing_deadline(
            bill, hearing, reply, today=plan.today
        ).message_id

    def _send(self, pub_id: int, bill: Bill, send: Send) -> int | None:
        pub = self._repo.publication_by_id(pub_id)
        if (
            pub is not None
            and pub.delivery is not None
            and (pub.kind not in FROZEN_REPLIES or pub.status is PublicationStatus.QUEUED)
            and not pub.delivery.rendered_text
            and not pub.attempts
        ):
            if pub.kind in FROZEN_REPLIES and (
                pub.kind is PublicationKind.STATUS_UPDATE or pub.delivery.edit_message_id is None
            ):
                pub = self._freeze(pub, keep_today=pub.kind is not PublicationKind.STATUS_UPDATE)
            else:
                assert pub.delivery is not None, (
                    "stored rows selected for freezing retain their plan"
                )
                sitting = (
                    pub.kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED)
                    and pub.status is PublicationStatus.QUEUED
                )
                text = (
                    self._publisher.render_sittings((SittingNews.of(pub.kind, pub.delivery),))
                    if sitting
                    else self._publisher.render_delivery(pub)
                )
                if utf16_length(text) > MESSAGE_LIMIT:
                    pub = (
                        self._freeze_sitting(pub, text)
                        if sitting
                        else freeze_parts(self._repo, self._clock, self._publisher, pub, text)
                    )
        if pub is not None and pub.delivery is not None and pub.delivery.rendered_text:

            def prepared_send(_: int | None) -> int:
                return self._publisher.publish_prepared(pub).message_id

            send = prepared_send
        card = self.card(bill)
        reply_to = card.message_id if card else None
        checkpoint = self._delivery_checkpoint(pub)
        self._repo.mark_publication(pub_id, PublicationStatus.PENDING, count_attempt=False)
        sent = send_publication(
            self._repo,
            self._clock,
            pub_id,
            lambda: send(reply_to),
            checkpoint=checkpoint,
        )

        self._count_attention((pub_id,), sent)
        if sent is not None and pub is not None and pub.delivery is not None:
            parts = send_continuations(
                self._repo,
                self._clock,
                self._publisher,
                pub,
                self._attempted,
                checkpoint=checkpoint,
            )
            self._part_errors.extend(parts.errors)
            count = parts.sent + (
                pub.kind is PublicationKind.MESSAGE_PART and pub.delivery.edit_message_id is None
            )
            self._attention_metrics["continuation_messages"] = (
                self._attention_metrics.get("continuation_messages", 0) + count
            )
        return sent

    def _delivery_checkpoint(self, pub: Publication | None) -> StateCheckpoint | None:
        if pub is None:
            return None
        root = (
            self._repo.publication_by_id(pub.delivery.continuation_root)
            if pub.delivery is not None and pub.delivery.continuation_root is not None
            else pub
        )
        if root is not None and root.kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL):
            return self._checkpoint
        if (
            root is not None
            and root.kind
            in (
                PublicationKind.CONSULTATION_REVIEW,
                PublicationKind.COMMISSION_REVIEW,
                PublicationKind.AMENDMENTS_REVISION,
            )
            and root.delivery is not None
            and root.delivery.rendered_text
        ):
            return self._checkpoint
        if root is not None and card_revision_owner(self._repo, root) is not None:
            return self._checkpoint
        if (
            root is not None
            and root.kind is PublicationKind.MESSAGE_PART
            and root.delivery is not None
            and root.delivery.revision_of is not None
            and root.delivery.edit_message_id is not None
        ):
            raise ValueError("The saved card revision has no matching delivered owner")
        if root is not None and root.kind in (
            PublicationKind.AGENDA,
            PublicationKind.AGENDA_CANCELLED,
        ):
            return self._checkpoint
        if (
            root is not None
            and root.kind in FROZEN_REPLIES
            and root.delivery is not None
            and (
                root.delivery.edit_message_id is None
                or (root.kind is PublicationKind.STATUS_UPDATE and root.delivery.rendered_text)
            )
        ):
            return self._checkpoint
        return None

    def delivery_errors(self) -> list[str]:
        return list(self._part_errors)

    def attention_metrics(self) -> dict[str, int]:
        metrics = dict(self._attention_metrics)
        digest_queue = [
            pub
            for pub in self._repo.list_open_deliveries(self._channel_id)
            if pub.delivery is not None and pub.delivery.destination == "digest_only"
        ]
        if digest_queue:
            metrics["digest_queued_events"] = len(digest_queue)
            metrics["digest_awaiting_approval"] = sum(
                pub.id is not None and self._repo.digest_owner(pub.id) is None
                for pub in digest_queue
            )
            metrics["digest_oldest_minutes"] = max(
                max(0, int((self._clock.now() - pub.created_at).total_seconds() // 60))
                for pub in digest_queue
            )
        schedules = [
            pub.delivery.schedule
            for pub in self._repo.list_scheduled_deliveries(self._channel_id)
            if pub.delivery is not None and pub.delivery.schedule is not None
        ]
        if schedules:
            now = self._clock.now()
            metrics["daily_queued"] = len(schedules)
            metrics["daily_oldest_minutes"] = max(
                max(0, int((now - schedule.detected_at).total_seconds() // 60))
                for schedule in schedules
            )
            overdue = sum(now > schedule.deliver_by for schedule in schedules)
            if overdue:
                metrics["daily_overdue"] = metrics.get("daily_overdue", 0) + overdue
        return metrics

    def _count_attention(self, identities: tuple[int, ...], sent: int | None) -> None:
        if sent is None:
            return
        messages = False
        for identity in identities:
            if identity in self._counted:
                continue
            self._counted.add(identity)
            pub = self._repo.publication_by_id(identity)
            if pub is None:
                continue
            if pub.kind is PublicationKind.STATUS_UPDATE and pub.delivery is not None:
                snapshot = Bill.model_validate_json(pub.delivery.bill_json)
                chosen = snapshot.editorial.mnemonic
                current = self._repo.get(pub.term, pub.number)
                if (
                    chosen is not None
                    and current is not None
                    and current.editorial.mnemonic == chosen
                ):
                    self._repo.save_editorial(
                        pub.term,
                        pub.number,
                        current.editorial.model_copy(update={"announced_version": chosen.version}),
                    )
            if pub.attention is None:
                continue
            assessment = pub.attention
            if (
                assessment.deadline is not None
                and assessment.action != "wait"
                and pub.kind in (PublicationKind.STATUS_UPDATE, PublicationKind.ACT_PUBLISHED)
            ):
                self._told_windows[
                    (pub.term, pub.number, assessment.action, assessment.deadline)
                ] = (identity, sent)
            keys = ["events", assessment.importance]
            if pub.delivery is not None and pub.delivery.schedule is not None:
                keys.append("daily_released")
                if self._clock.now() > pub.delivery.schedule.deliver_by:
                    keys.append("daily_overdue")
            if assessment.urgency == "urgent":
                keys.append("urgent")
            if len(identities) > 1:
                keys.append("grouped")
            if (
                assessment.deadline is not None
                and assessment.deadline
                < self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date()
                and assessment.action != "wait"
            ):
                keys.append("overdue")
            keys.extend(f"reason/{reason}" for reason in assessment.reasons)
            for key in keys:
                self._attention_metrics[key] = self._attention_metrics.get(key, 0) + 1
            messages = messages or pub.delivery is None or pub.delivery.edit_message_id is None
        if messages:
            self._attention_metrics["messages"] = self._attention_metrics.get("messages", 0) + 1
