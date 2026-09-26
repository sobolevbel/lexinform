import re
from dataclasses import dataclass

from lexinform_web.matters.models import SourceIdentity


@dataclass(frozen=True)
class SourceKey:
    source: SourceIdentity.Source
    scope: str
    external_id: str

    def __post_init__(self) -> None:
        if not self.scope.strip() or not self.external_id.strip():
            raise ValueError("source identity requires a nonempty scope and external id")
        if self.scope != self.scope.strip() or self.external_id != self.external_id.strip():
            raise ValueError("source identity must be normalized before persistence")
        if self.source == SourceIdentity.Source.RCL and self.scope != "global":
            raise ValueError("RCL identities do not belong to a Sejm term")
        if self.source in {
            SourceIdentity.Source.RCL,
            SourceIdentity.Source.SEJM,
        } and not re.fullmatch(r"[1-9]\d*", self.external_id):
            raise ValueError("Sejm and RCL require a normalized numeric identifier")
        if self.source == SourceIdentity.Source.SEJM and not re.fullmatch(r"[1-9]\d*", self.scope):
            raise ValueError("Sejm scope must be a positive term number")
        if self.source == SourceIdentity.Source.RPW and (
            not re.fullmatch(r"RPW/\d+/\d{4}", self.external_id) or self.scope != "global"
        ):
            raise ValueError("RPW requires its full number including year and global scope")
        if self.source == SourceIdentity.Source.WYKAZ and re.fullmatch(
            r"(?:WPL/)?[A-Z]+\d+", self.external_id
        ):
            raise ValueError("a Wykaz display number alone is not a stable entry identity")
