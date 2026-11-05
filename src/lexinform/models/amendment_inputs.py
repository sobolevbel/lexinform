"""Immutable amendment questions preserve actual text input without a byte-admission policy."""

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lexinform.json_storage import canonical_json, read_json
from lexinform.models.analysis import AmendmentsContext
from lexinform.models.computation_inputs import ComputationInputBinding
from lexinform.models.input_receipts import AmendmentInputReceipt


class AmendmentInputSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: Literal["amendments-text-question-v1"] = "amendments-text-question-v1"
    question_json: str

    @property
    def context(self) -> AmendmentsContext:
        value = read_json(self.question_json)
        if (
            not isinstance(value, dict)
            or set(value) != {"kind", "ctx"}
            or value["kind"] != "amendments"
        ):
            raise ValueError("invalid_amendment_input")
        context = AmendmentsContext.model_validate(value["ctx"])
        if context.scan is not None or not isinstance(value["ctx"], dict):
            raise ValueError("invalid_amendment_input")
        if canonical_json(context.model_dump(mode="json", exclude_unset=True)) != canonical_json(
            value["ctx"]
        ):
            raise ValueError("invalid_amendment_input")
        return context

    @property
    def question_sha256(self) -> str:
        return hashlib.sha256(self.question_json.encode()).hexdigest()

    @property
    def snapshot_id(self) -> str:
        return (
            "amendment-input-v1:"
            + hashlib.sha256((self.profile + "\n" + self.question_json).encode()).hexdigest()
        )

    @model_validator(mode="after")
    def typed_canonical_text(self) -> Self:
        try:
            if canonical_json(read_json(self.question_json)) != self.question_json:
                raise ValueError("invalid_amendment_input")
            _ = self.context
        except ValueError, TypeError, RecursionError:
            raise ValueError("invalid_amendment_input") from None
        return self


class AmendmentInputRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(pattern=r"^amendment-input-v1:[0-9a-f]{64}$")
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lineage_receipt: AmendmentInputReceipt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    lineage_result_id: str | None = Field(
        default=None, pattern=r"^result-v1:[0-9a-f]{64}$", exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def actual_lineage(self) -> Self:
        if self.lineage_result_id is not None and self.lineage_receipt is None:
            raise ValueError("invalid_amendment_input")
        if (
            self.lineage_receipt is not None
            and self.lineage_receipt.request_sha256 != self.request_sha256
            and self.lineage_result_id is None
        ):
            raise ValueError("invalid_amendment_input")
        return self

    def require_carrier(
        self, purpose: str, request_sha256: str | None, binding: ComputationInputBinding | None
    ) -> None:
        if purpose != "amendments" or request_sha256 != self.request_sha256 or binding is None:
            raise ValueError("invalid_amendment_input")


class AmendmentInputAvailability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["available", "unavailable"]
    reason: (
        Literal[
            "root_history_unavailable", "root_amendment_unavailable", "legacy_input_unavailable"
        ]
        | None
    ) = None
    root_content_id: str | None = None
    result_id: str | None = None
    snapshot: AmendmentInputSnapshot | None = None
