"""The `Publisher` port implemented once over a formatter: every message kind is rendered here,
and a concrete publisher only says how a rendered message leaves (Telegram, stdout).

Adding a message kind means one formatter method, one `Publisher` method here and nothing in the
concrete publishers.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import AgendaItem, Bill, PrintInfo, PublicationKind, Stage, StatusChange
from lexinform.ports import PublishResult


@dataclass(frozen=True)
class Outgoing:
    """A rendered message on its way out: what it is about, its text, where it replies."""

    kind: PublicationKind
    bill: Bill
    text: str
    reply_to: int | None = None
    detail: str = ""  # a word for logs and dry runs: the thread joined, the sitting


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

    def card_digest(self, bill: Bill) -> str:
        text = self._formatter.new_bill(bill, None).text
        return hashlib.sha256(text.encode()).hexdigest()

    def publish_joint_bill(
        self, bill: Bill, primary: Bill, print_info: PrintInfo | None, reply_to: int | None
    ) -> PublishResult:
        text = self._formatter.joint_bill(bill, primary, print_info).text
        detail = f"under druk {primary.number}"
        return self._deliver(Outgoing(PublicationKind.JOINT_BILL, bill, text, reply_to, detail))

    def publish_status_update(
        self, bill: Bill, change: StatusChange, reply_to: int | None
    ) -> PublishResult:
        text = self._formatter.status_update(bill, change).text
        return self._deliver(Outgoing(PublicationKind.STATUS_UPDATE, bill, text, reply_to))

    def publish_act_published(self, bill: Bill, reply_to: int | None) -> PublishResult:
        text = self._formatter.act_published(bill).text
        return self._deliver(Outgoing(PublicationKind.ACT_PUBLISHED, bill, text, reply_to))

    def publish_in_force(self, bill: Bill, reply_to: int | None) -> PublishResult:
        text = self._formatter.in_force(bill).text
        return self._deliver(Outgoing(PublicationKind.IN_FORCE, bill, text, reply_to))

    def publish_consultation_deadline(
        self, bill: Bill, reply_to: int | None, *, today: date
    ) -> PublishResult:
        text = self._formatter.consultation_deadline(bill, today=today).text
        return self._deliver(Outgoing(PublicationKind.CONSULTATION_DEADLINE, bill, text, reply_to))

    def publish_consultation_results(self, bill: Bill, reply_to: int | None) -> PublishResult:
        text = self._formatter.consultation_results(bill).text
        return self._deliver(Outgoing(PublicationKind.CONSULTATION_RESULTS, bill, text, reply_to))

    def publish_agenda(
        self, bill: Bill, item: AgendaItem, reply_to: int | None, moved_from: date | None = None
    ) -> PublishResult:
        text = self._formatter.agenda(bill, item, moved_from=moved_from).text
        return self._deliver(Outgoing(PublicationKind.AGENDA, bill, text, reply_to, item.ref))

    def publish_hearing_deadline(
        self, bill: Bill, hearing: Stage, reply_to: int | None, *, today: date
    ) -> PublishResult:
        text = self._formatter.hearing_deadline(bill, hearing, today=today).text
        return self._deliver(Outgoing(PublicationKind.HEARING_DEADLINE, bill, text, reply_to))
