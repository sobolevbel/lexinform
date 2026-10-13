import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lexinform.models.attention import AttentionAssessment
from lexinform.models.delivery import DeliverySchedule
from lexinform.models.enums import PublicationKind
from lexinform.models.evidence import decision_fingerprint
from lexinform.models.participation import ParticipationSnapshot
from lexinform.models.sejm import Stage, TextDocument, stage_fingerprint


class ObservedProcess(BaseModel):
    model_config = ConfigDict(frozen=True)

    stages: tuple[Stage, ...] = ()
    closure_date: dt.date | None = None
    passed: bool | None = None
    document: TextDocument | None = None
    analysis_sha256: str | None = None
    analysis_revision: int = 0
    seen_supplements: tuple[str, ...] | None = None

    @property
    def fingerprint(self) -> str:
        return f"{stage_fingerprint(self.stages)}:{decision_fingerprint(self.stages)}:{self.passed}"


class UpdateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    publication_id: int
    bill_json: str
    change_json: str
    card_message_id: int | None = None


class DeliveryPlan(BaseModel):
    """Serialized snapshots cannot be mutated by a later watcher or a delivery attempt."""

    model_config = ConfigDict(frozen=True)

    bill_json: str
    attention: AttentionAssessment | None = None
    attention_history: tuple[AttentionAssessment, ...] = ()
    policy_version: str = "legacy"
    rendered_text: str = ""
    disable_notification: bool = False
    schedule: DeliverySchedule | None = None
    destination: Literal["direct", "digest_only"] = "direct"
    destination_reason: str = ""
    proposed_destination: Literal["direct", "digest_only"] | None = None
    digest_eligible_at: dt.datetime | None = None
    digest_event_ids: tuple[int, ...] = ()
    participation: ParticipationSnapshot | None = None
    change_json: str = ""
    print_json: str | None = None
    primary_json: str | None = None
    item_json: str | None = None
    moved_from_json: str | None = None
    phase_json: str | None = None
    hearing_json: str | None = None
    today: dt.date | None = None
    still_meets: bool = False
    reply_to: int | None = None
    edit_message_id: int | None = None
    held_change_ids: tuple[int, ...] = ()
    wait_until: dt.datetime | None = None
    absorbed_by: int | None = None
    update_group: tuple[UpdateSnapshot, ...] = ()
    sitting_group: tuple[int, ...] = ()
    digest_parts: tuple[int, ...] = ()
    digest_ref: str = ""
    approve_label: str = ""
    group_text: str = ""
    with_act: bool = False
    """A status update that also tells the act published in the same run."""

    def replayable(self, kind: PublicationKind) -> bool:
        if kind is PublicationKind.DIGEST:
            return bool(self.rendered_text)
        if self.sitting_group:
            return bool(self.group_text)
        if kind is PublicationKind.STATUS_UPDATE and self.update_group:
            return bool(self.group_text) and all(item.change_json for item in self.update_group)
        if kind is PublicationKind.NEW_BILL:
            return True
        if self.reply_to is None:
            return False
        if kind is PublicationKind.JOINT_BILL:
            return bool(self.primary_json)
        if kind is PublicationKind.STATUS_UPDATE:
            return bool(self.change_json)
        if kind in (PublicationKind.ACT_PUBLISHED, PublicationKind.CONSULTATION_RESULTS):
            return True
        if kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED):
            return bool(self.item_json)
        if self.today is None:
            return False
        if kind in (PublicationKind.IN_FORCE, PublicationKind.CONSULTATION_DEADLINE):
            return True
        if kind is PublicationKind.DECISION_DEADLINE:
            return bool(self.phase_json)
        return kind is PublicationKind.HEARING_DEADLINE and bool(self.hearing_json)

    @property
    def publication_ids(self) -> tuple[int, ...]:
        return tuple(item.publication_id for item in self.update_group) or self.sitting_group
