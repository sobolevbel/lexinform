"""The `Publisher` port implemented once over a formatter: every message kind is rendered here,
and a concrete publisher only says how a rendered message leaves (Telegram, stdout).

Adding a message kind means one formatter method, one `Publisher` method here and nothing in the
concrete publishers.
"""

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.direct_delivery import DIRECT_ROOTS, direct_root_plan, validate_reader_contract
from lexinform.models import (
    DIGEST_NUMBER,
    Bill,
    Digest,
    DigestPart,
    Phase,
    PrintInfo,
    Publication,
    PublicationKind,
    SittingNews,
    Stage,
    StatusChange,
    UpdateNews,
)
from lexinform.ports import PublishResult
from lexinform.sitting_delivery import SITTING_KINDS, validate_sitting_contract


@dataclass(frozen=True)
class Outgoing:
    """A rendered message on its way out: what it is about, its text, where it replies.

    `bill` is None for the digest and the sittings roundup; `action` is the digest's button in the
    technical channel — a label and the data a press sends back (`digest:2026-W38`)."""

    kind: PublicationKind
    bill: Bill | None
    text: str
    reply_to: int | None = None
    detail: str = ""  # a word for logs and dry runs: the thread joined, the sitting
    action: tuple[str, str] | None = None

    @property
    def number(self) -> str:
        """What the post is about, for a log line or a dry run's title."""
        return self.bill.number if self.bill is not None else DIGEST_NUMBER


@dataclass(frozen=True)
class PreparedPublishResult:
    message_id: int
    document_message_ids: list[int] = field(default_factory=list)


class RenderingPublisher(ABC):
    """Renders each message kind with the formatter; subclasses deliver the result."""

    def __init__(self, formatter: MessageFormatter) -> None:
        self._formatter = formatter

    @abstractmethod
    def _deliver(self, message: Outgoing) -> PublishResult:
        """Send a rendered message and return the id it got."""

    @abstractmethod
    def _edit(self, message: Outgoing, *, message_id: int) -> None:
        """Replace the message `message_id` with a rendered one."""

    def publish_new_bill(self, bill: Bill, print_info: PrintInfo | None) -> PublishResult:
        text = self._formatter.new_bill(bill, print_info).text
        return self._deliver(Outgoing(PublicationKind.NEW_BILL, bill, text))

    def edit_new_bill(self, bill: Bill, print_info: PrintInfo | None, *, message_id: int) -> None:
        text = self._formatter.new_bill(bill, print_info).text
        self._edit(Outgoing(PublicationKind.NEW_BILL, bill, text), message_id=message_id)

    def render_card_parts(
        self, bill: Bill, print_info: PrintInfo | None, *, minimum: int, today: date | None = None
    ) -> tuple[str, ...]:
        parts = self.split_message(self._formatter.new_bill(bill, print_info, today=today).text)
        return parts + (self._formatter.superseded_card_part(),) * max(0, minimum - len(parts))

    def card_digest(self, bill: Bill, *, today: date | None = None) -> str:
        text = self._formatter.new_bill(bill, None, today=today).text
        return hashlib.sha256(text.encode()).hexdigest()

    def publish_joint_bill(
        self, bill: Bill, primary: Bill, print_info: PrintInfo | None, reply_to: int | None
    ) -> PublishResult:
        text = self._formatter.joint_bill(bill, primary, print_info).text
        detail = f"under druk {primary.number}"
        return self._deliver(Outgoing(PublicationKind.JOINT_BILL, bill, text, reply_to, detail))

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None, *, with_act: bool = False
    ) -> PublishResult:
        text = self._formatter.status_update(bill, change, with_act=with_act).text
        return self._deliver(Outgoing(PublicationKind.STATUS_UPDATE, bill, text, reply_to))

    def edit_status_update(
        self,
        bill: Bill,
        change: StatusChange,
        *,
        message_id: int,
        today: date,
        with_act: bool = False,
    ) -> None:
        text = self._formatter.status_update(bill, change, today=today, with_act=with_act).text
        self._edit(Outgoing(PublicationKind.STATUS_UPDATE, bill, text), message_id=message_id)

    def publish_act_published(self, bill: Bill, reply_to: int | None) -> PublishResult:
        text = self._formatter.act_published(bill).text
        return self._deliver(Outgoing(PublicationKind.ACT_PUBLISHED, bill, text, reply_to))

    def publish_in_force(
        self, bill: Bill, reply_to: int | None, *, today: date | None = None
    ) -> PublishResult:
        text = self._formatter.in_force(bill, today=today).text
        return self._deliver(Outgoing(PublicationKind.IN_FORCE, bill, text, reply_to))

    def publish_consultation_deadline(
        self, bill: Bill, reply_to: int | None, *, today: date
    ) -> PublishResult:
        text = self._formatter.consultation_deadline(bill, today=today).text
        return self._deliver(Outgoing(PublicationKind.CONSULTATION_DEADLINE, bill, text, reply_to))

    def publish_consultation_results(self, bill: Bill, reply_to: int | None) -> PublishResult:
        text = self._formatter.consultation_results(bill).text
        return self._deliver(Outgoing(PublicationKind.CONSULTATION_RESULTS, bill, text, reply_to))

    def publish_sittings(self, news: Sequence[SittingNews]) -> PublishResult:
        text = self._formatter.sittings(news).text
        detail = ", ".join(dict.fromkeys(n.bill.number for n in news))
        return self._deliver(Outgoing(PublicationKind.AGENDA, None, text, detail=detail))

    def sittings_fit(self, news: Sequence[SittingNews]) -> bool:
        return self._formatter.sittings_fit(news)

    def render_updates(self, news: Sequence[UpdateNews], *, today: date) -> str:
        return self._formatter.updates(news, today=today)

    def updates_fit(self, news: Sequence[UpdateNews], *, today: date) -> bool:
        return self._formatter.updates_fit(news, today=today)

    def publish_updates(self, text: str) -> PublishResult:
        return self._deliver(Outgoing(PublicationKind.STATUS_UPDATE, None, text))

    def render_delivery(self, publication: Publication) -> str:
        if (
            publication.kind in DIRECT_ROOTS
            and publication.delivery is not None
            and publication.delivery.render_origin == "legacy_snapshot"
            and not publication.delivery.rendered_text
            and not publication.delivery.publication_ids
            and not publication.delivery.group_text
        ):
            return self._render_direct_delivery(publication)
        return self._formatter.delivery_preview(publication)

    def _render_direct_delivery(self, publication: Publication) -> str:
        plan = direct_root_plan(publication)
        bill = Bill.model_validate_json(plan.bill_json)
        kind = publication.kind
        info = PrintInfo.model_validate_json(plan.print_json) if plan.print_json else None
        if kind is PublicationKind.NEW_BILL:
            return self._formatter.new_bill(bill, info).text
        if kind is PublicationKind.JOINT_BILL:
            assert plan.primary_json is not None, "direct_root_plan requires the primary bill"
            return self._formatter.joint_bill(
                bill, Bill.model_validate_json(plan.primary_json), info
            ).text
        if kind is PublicationKind.STATUS_UPDATE:
            return self._formatter.status_update(
                bill,
                StatusChange.model_validate_json(plan.change_json),
                today=plan.today if plan.edit_message_id is not None else None,
                with_act=plan.with_act,
            ).text
        if kind is PublicationKind.ACT_PUBLISHED:
            return self._formatter.act_published(bill).text
        if kind is PublicationKind.CONSULTATION_RESULTS:
            return self._formatter.consultation_results(bill).text
        assert plan.today is not None, "direct_root_plan requires the reminder's original date"
        if kind is PublicationKind.IN_FORCE:
            return self._formatter.in_force(bill, today=plan.today).text
        if kind is PublicationKind.CONSULTATION_DEADLINE:
            return self._formatter.consultation_deadline(bill, today=plan.today).text
        if kind is PublicationKind.DECISION_DEADLINE:
            assert plan.phase_json is not None, "direct_root_plan requires the saved phase"
            return self._formatter.decision_deadline(
                bill, Phase.model_validate_json(plan.phase_json), today=plan.today
            ).text
        assert kind is PublicationKind.HEARING_DEADLINE and plan.hearing_json is not None, (
            "direct_root_plan admits only the six supported notice callbacks"
        )
        return self._formatter.hearing_deadline(
            bill, Stage.model_validate_json(plan.hearing_json), today=plan.today
        ).text

    def split_message(self, text: str) -> tuple[str, ...]:
        return self._formatter.message_parts(text)

    def render_sittings(self, news: Sequence[SittingNews]) -> str:
        return self._formatter.sittings(news).text

    def render_digest(self, digest: Digest, *, draft: bool = False) -> tuple[str, ...]:
        return self._formatter.digest_parts(digest, draft=draft)

    def render_digest_parts(self, digest: Digest, *, draft: bool = False) -> tuple[DigestPart, ...]:
        return self._formatter.digest_layout(digest, draft=draft)

    def publish_prepared(self, publication: Publication) -> PublishResult:
        validate_reader_contract(publication)
        if publication.kind in SITTING_KINDS:
            validate_sitting_contract(publication)
        plan = publication.delivery
        assert plan is not None and (plan.rendered_text or plan.group_text)
        bill = Bill.model_validate_json(plan.bill_json) if plan.bill_json else None
        outgoing = Outgoing(
            PublicationKind.AGENDA if plan.sitting_group else publication.kind,
            None if plan.sitting_group else bill,
            plan.group_text or plan.rendered_text,
            None if publication.kind in SITTING_KINDS else plan.reply_to,
            Phase.model_validate_json(plan.phase_json).key
            if publication.kind is PublicationKind.DECISION_DEADLINE and plan.phase_json
            else "",
            action=(plan.approve_label, f"digest:{plan.digest_ref}")
            if plan.approve_label
            else None,
        )
        if plan.edit_message_id is not None:
            self._edit(outgoing, message_id=plan.edit_message_id)
            return PreparedPublishResult(message_id=plan.edit_message_id)
        return self._deliver(outgoing)

    def publish_hearing_deadline(
        self, bill: Bill, hearing: Stage, reply_to: int | None, *, today: date
    ) -> PublishResult:
        text = self._formatter.hearing_deadline(bill, hearing, today=today).text
        return self._deliver(Outgoing(PublicationKind.HEARING_DEADLINE, bill, text, reply_to))

    def publish_decision_deadline(
        self, bill: Bill, phase: Phase, reply_to: int | None, *, today: date
    ) -> PublishResult:
        text = self._formatter.decision_deadline(bill, phase, today=today).text
        detail = phase.key
        return self._deliver(
            Outgoing(PublicationKind.DECISION_DEADLINE, bill, text, reply_to, detail)
        )

    def publish_digest(self, digest: Digest, *, approve: str = "") -> PublishResult:
        text = self._formatter.digest(digest, draft=bool(approve)).text
        action = (approve, f"{PublicationKind.DIGEST.value}:{digest.ref}") if approve else None
        return self._deliver(
            Outgoing(PublicationKind.DIGEST, None, text, detail=digest.ref, action=action)
        )
