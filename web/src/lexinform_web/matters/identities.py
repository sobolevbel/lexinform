import re
from dataclasses import dataclass

from django.db.models import TextChoices


class IdentitySource(TextChoices):
    SEJM = "sejm", "Сейм"
    RPW = "rpw", "RPW"
    RCL = "rcl", "RCL"
    WYKAZ = "wykaz", "Wykaz"


@dataclass(frozen=True)
class SourceKey:
    source: IdentitySource
    scope: str
    external_id: str

    def __post_init__(self) -> None:
        if self.source not in {"sejm", "rcl", "rpw", "wykaz"}:
            raise ValueError("unsupported source identity")
        if not self.scope.strip() or not self.external_id.strip():
            raise ValueError("source identity requires a nonempty scope and external id")
        if self.scope != self.scope.strip() or self.external_id != self.external_id.strip():
            raise ValueError("source identity must be normalized before persistence")
        if self.source == "rcl" and self.scope != "global":
            raise ValueError("RCL identities do not belong to a Sejm term")
        if self.source in {
            "rcl",
            "sejm",
        } and not re.fullmatch(r"[1-9]\d*", self.external_id):
            raise ValueError("Sejm and RCL require a normalized numeric identifier")
        if self.source == "sejm" and not re.fullmatch(r"[1-9]\d*", self.scope):
            raise ValueError("Sejm scope must be a positive term number")
        if self.source == "rpw" and (
            not re.fullmatch(r"RPW/\d+/\d{4}", self.external_id) or self.scope != "global"
        ):
            raise ValueError("RPW requires its full number including year and global scope")
        if self.source == "wykaz" and re.fullmatch(r"(?:WPL/)?[A-Z]+\d+", self.external_id):
            raise ValueError("a Wykaz display number alone is not a stable entry identity")
