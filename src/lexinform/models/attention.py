import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AttentionAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    importance: Literal["important", "routine", "uncertain"] = "uncertain"
    urgency: Literal["urgent", "normal", "uncertain"] = "uncertain"
    reasons: tuple[str, ...] = ()
    explanation: str = ""
    affected_groups: tuple[str, ...] = ()
    action: str = "wait"
    deadline: dt.date | None = None
    deadline_source: str | None = None
    deadline_precision: Literal["day", "estimated", "unknown"] = "unknown"
    detected_at: dt.datetime
    assessed_at: dt.datetime
    novelty: Literal[
        "new_decision", "new_consequence", "new_participation", "enrichment", "unknown"
    ] = "unknown"
    rule_version: str = "attention-v1"

    @property
    def protected(self) -> bool:
        return (
            self.importance != "routine"
            or self.urgency != "normal"
            or "pilny_guard" in self.reasons
        )
