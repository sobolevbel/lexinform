from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict


class DeliverySchedule(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: Literal["daily_updates", "daily_sittings"]
    detected_at: AwareDatetime
    not_before: AwareDatetime
    deliver_by: AwareDatetime
