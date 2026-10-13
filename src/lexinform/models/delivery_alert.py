import datetime as dt

from pydantic import BaseModel, ConfigDict


class DeliveryAlert(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_id: str
    fingerprint: str
    notified_at: dt.datetime
