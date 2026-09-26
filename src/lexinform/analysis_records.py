from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from lexinform.models import (
    Amendments,
    AmendmentsRecord,
    Analysis,
    AnalysisRecord,
    DocumentDigest,
    JointComparison,
    JointRecord,
    SupplementRecord,
)
from lexinform.models.enums import SourceKind, TextSource


class UsageFields(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    cache_read_input_tokens: int | None
    cache_creation_input_tokens: int | None


class RecordFields(UsageFields):
    model: str
    prompt_version: str
    created_at: datetime


@dataclass(frozen=True)
class RecordFactory:
    model: str
    prompt_version: str
    created_at: datetime
    usage: UsageFields

    def fields(self) -> RecordFields:
        return RecordFields(
            **self.usage,
            model=self.model,
            prompt_version=self.prompt_version,
            created_at=self.created_at,
        )

    def analysis(
        self, answer: Analysis, *, input_chars: int, truncated: bool, text_source: TextSource
    ) -> AnalysisRecord:
        return AnalysisRecord(
            analysis=answer,
            input_chars=input_chars,
            truncated=truncated,
            text_source=text_source,
            **self.fields(),
        )

    def amendments(self, answer: Amendments, *, source_kind: SourceKind) -> AmendmentsRecord:
        return AmendmentsRecord(
            amendments=answer, source_url="", source_kind=source_kind, **self.fields()
        )

    def supplement(
        self, answer: DocumentDigest, *, title: str, source_kind: SourceKind
    ) -> SupplementRecord:
        return SupplementRecord(
            digest=answer,
            number="",
            title=title,
            source_url="",
            source_kind=source_kind,
            **self.fields(),
        )

    def joint(self, answer: JointComparison, *, compared_with: list[str]) -> JointRecord:
        return JointRecord(comparison=answer, compared_with=compared_with, **self.fields())
