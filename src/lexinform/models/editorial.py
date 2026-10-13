import datetime as dt
import re

from pydantic import BaseModel, ConfigDict


def valid_mnemonic(value: str) -> bool:
    return (
        bool(value.strip())
        and value == " ".join(value.split())
        and len(value.split()) <= 4
        and not re.search(r"[<>#\r\n\t]|https?://|www\.", value, re.IGNORECASE)
    )


class Mnemonic(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    language: str
    basis: str
    selected_at: dt.datetime
    origin: str = "analysis"
    version: int = 1
    provisional: bool = False
    command_update_id: int | None = None


class PriorityOverride(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str
    expires_at: dt.datetime
    command_update_id: int
    selected_at: dt.datetime
    urgent: bool = False
    important: bool = False


class Editorial(BaseModel):
    model_config = ConfigDict(frozen=True)

    mnemonic: Mnemonic | None = None
    previous: tuple[Mnemonic, ...] = ()
    proposed: Mnemonic | None = None
    priority: PriorityOverride | None = None
    priority_history: tuple[PriorityOverride, ...] = ()

    def select(self, candidate: Mnemonic) -> Editorial:
        if not valid_mnemonic(candidate.name):
            return self
        if self.mnemonic is None:
            return self.model_copy(update={"mnemonic": candidate})
        if self.mnemonic.name == candidate.name:
            return self
        return self.model_copy(update={"proposed": candidate})

    def rename(self, candidate: Mnemonic) -> Editorial:
        previous = self.previous + ((self.mnemonic,) if self.mnemonic else ())
        return self.model_copy(
            update={
                "mnemonic": candidate.model_copy(update={"version": len(previous) + 1}),
                "previous": previous,
                "proposed": None,
            }
        )
