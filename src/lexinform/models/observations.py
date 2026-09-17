import datetime as dt

from pydantic import BaseModel, ConfigDict

from lexinform.models.evidence import decision_fingerprint
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
        return f"{stage_fingerprint(self.stages)}:{decision_fingerprint(self.stages)}"


class DeliveryPlan(BaseModel):
    """Serialized snapshots cannot be mutated by a later watcher or a delivery attempt."""

    model_config = ConfigDict(frozen=True)

    bill_json: str
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
    held_change_ids: tuple[int, ...] = ()
