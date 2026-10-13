import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ConsultationWindow(BaseModel):
    """A Sejm survey or RCL consultation letter defines the public participation window."""

    model_config = ConfigDict(frozen=True)

    source: Literal["sejm", "rcl"]
    start: dt.date | None = None
    end: dt.date | None = None
    form_url: str | None = None
    survey_url: str | None = None
    email: str | None = None
    letter_url: str | None = None
    results_published: bool = False
    days: int | None = None

    def is_open(self, today: dt.date) -> bool:
        return self.end is not None and self.end >= today


class ParticipationSnapshot(BaseModel):
    """An explicit missing window is evidence separate from the historical event snapshot."""

    model_config = ConfigDict(frozen=True)

    consultation: ConsultationWindow | None
    checked_at: dt.datetime
