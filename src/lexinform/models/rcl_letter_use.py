"""Bounded RCL letter parse and in-memory merge facts do not certify saved applications."""

import datetime as dt
import hashlib
import json
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexinform.models.artifacts import ArtifactIdentity, ArtifactReadIdentity
from lexinform.models.extractions import ExtractionResult

MAX_RCL_LETTER_USE_BYTES = 4096
MAX_RCL_LETTER_OMISSION_BYTES = 2048
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SqlInteger = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
OfficialId = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
OmissionReason = Literal[
    "descriptor_name_bytes",
    "url_bytes",
    "email_bytes",
    "error_class_bytes",
    "python_bytes",
    "record_bytes",
]
_MAX_TIME = dt.datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=dt.UTC)


def rcl_letter_canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def rcl_letter_sha256(value: object) -> str:
    return hashlib.sha256(rcl_letter_canonical(value)).hexdigest()


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


def _safe_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (
            not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            and url.scheme == "https"
            and url.hostname == "legislacja.rcl.gov.pl"
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and url.port in (None, 443)
        )
    except ValueError:
        return False


class RclLetterUrl(_Fact):
    value: str | None
    state: Literal["complete", "omitted_unsafe"]
    sha256: Sha256

    @classmethod
    def of(cls, value: str) -> Self:
        safe = _safe_url(value)
        return cls(
            value=value if safe else None,
            state="complete" if safe else "omitted_unsafe",
            sha256=hashlib.sha256(value.encode()).hexdigest(),
        )

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.value is None:
            if self.state != "omitted_unsafe":
                raise ValueError("an absent URL requires explicit omission")
        elif (
            self.state != "complete"
            or not _safe_url(self.value)
            or hashlib.sha256(self.value.encode()).hexdigest() != self.sha256
        ):
            raise ValueError("a retained RCL URL requires its exact safe identity")
        return self


class RclLetterDescriptor(_Fact):
    name: str
    url: RclLetterUrl
    created: dt.date | None = None


class RclLetterOfficial(_Fact):
    project_id: OfficialId
    stage_id: OfficialId | None
    stage_number: SqlInteger
    letter_id: OfficialId
    letter: RclLetterDescriptor


class RclLetterDateArgument(_Fact):
    value: dt.date | None
    source: Literal["letter.created", "stage.modified", "project.modified", "not_used"]

    @model_validator(mode="after")
    def paired(self) -> Self:
        if (self.value is None) != (self.source == "not_used"):
            raise ValueError("the date argument and fallback source must agree")
        return self


class RclLetterPhysical(_Fact):
    state: Literal["unknown_origin", "unknown_cached_body", "known_complete_body"]
    artifact_sha256: Sha256 | None = None
    artifact_bytes: SqlInteger | None = None
    artifact_read_id: str | None = Field(default=None, pattern=r"^artifact-read-v1:[0-9a-f]{64}$")
    extraction_id: str | None = Field(default=None, pattern=r"^extraction-v1:[0-9a-f]{64}$")
    input_extraction_relation: Literal["unknown", "exact_output_text_sha256"] = "unknown"

    @model_validator(mode="after")
    def coherent(self) -> Self:
        known = self.state == "known_complete_body"
        if known != (self.artifact_sha256 is not None and self.artifact_bytes is not None):
            raise ValueError("known origin requires a complete body identity and size")
        if not known and any(
            value is not None
            for value in (
                self.artifact_sha256,
                self.artifact_bytes,
                self.artifact_read_id,
                self.extraction_id,
            )
        ):
            raise ValueError("unknown origin cannot acquire inferred physical references")
        if self.input_extraction_relation != "unknown" and self.extraction_id is None:
            raise ValueError("exact extraction mapping requires an extraction reference")
        return self


class RclLetterInput(_Fact):
    sha256: Sha256
    chars: SqlInteger
    utf8_bytes: SqlInteger

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.utf8_bytes < self.chars:
            raise ValueError("UTF-8 input cannot have fewer bytes than characters")
        return self


class RclLetterComponents(_Fact):
    letter_parser: SqlInteger
    parser_sha256: Sha256 | None = None
    merge_sha256: Sha256 | None = None
    python: str | None = None


class RclLetterInfo(_Fact):
    letter_date: dt.date | None = None
    days: int | None = None
    deadline: dt.date | None = None
    email: str | None = None
    this_year: tuple[int, int] | None = None


class RclLetterWindow(_Fact):
    letter_url: RclLetterUrl | None = None
    letter_date: dt.date | None = None
    days: int | None = None
    deadline: dt.date | None = None
    email: str | None = None


class RclLetterWindowFact(_Fact):
    projection: RclLetterWindow
    canonical_projection_sha256: Sha256
    origin: Literal["in_memory_only_not_saved_application"] = "in_memory_only_not_saved_application"

    @classmethod
    def of(cls, window: RclLetterWindow) -> Self:
        url = window.letter_url
        if url is not None and url.value is None:
            raise ValueError("an omitted URL cannot reconstruct its original window hash")
        values = window.model_dump(mode="json")
        values["letter_url"] = url.value if url is not None else None
        return cls(projection=window, canonical_projection_sha256=rcl_letter_sha256(values))

    @model_validator(mode="after")
    def identity(self) -> Self:
        url = self.projection.letter_url
        if url is None or url.value is not None:
            values = self.projection.model_dump(mode="json")
            values["letter_url"] = url.value if url is not None else None
            if rcl_letter_sha256(values) != self.canonical_projection_sha256:
                raise ValueError("the retained window does not match its projection hash")
        return self


class RclLetterUsePayload(_Fact):
    contract: Literal["rcl-letter-use-v1"] = "rcl-letter-use-v1"
    scope: Literal["source_parse_in_memory_merge_not_application"] = (
        "source_parse_in_memory_merge_not_application"
    )
    official: RclLetterOfficial
    date_argument: RclLetterDateArgument
    physical: RclLetterPhysical
    input: RclLetterInput | None
    components: RclLetterComponents
    letter_info: RclLetterInfo | None
    result: RclLetterWindowFact | None
    before: RclLetterWindowFact | None
    after: RclLetterWindowFact | None
    outcome: Literal["returned", "no_text", "read_error", "raised"]
    error_class: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def reached_facts(self) -> Self:
        if (self.error_class is not None) != (self.outcome in ("read_error", "raised")):
            raise ValueError("failed reads require a class without exception prose")
        if self.outcome == "raised":
            if self.after is not None:
                raise ValueError("a propagated failure cannot invent a completed merge")
        elif self.result is None or self.after is None:
            raise ValueError("a returned read requires its result and completed merge")
        if self.outcome == "returned" and (self.input is None or self.letter_info is None):
            raise ValueError("a parsed result requires the exact parser input and output")
        if (self.letter_info is None) != (self.date_argument.source == "not_used"):
            raise ValueError("the deadline date argument must describe a reached parsed result")
        if self.input is None and (
            self.letter_info is not None or self.date_argument.source != "not_used"
        ):
            raise ValueError("an uncalled parser cannot have parsed output or a used date")
        if self.outcome in ("no_text", "read_error"):
            if self.input is not None or self.result is None:
                raise ValueError("an unreadable letter must return only its descriptor URL")
            value = self.result.projection
            if any(
                item is not None
                for item in (value.letter_date, value.days, value.deadline, value.email)
            ):
                raise ValueError("an unreadable letter cannot invent parser output")
        if (
            self.result is not None
            and self.result.projection.letter_url != self.official.letter.url
        ):
            raise ValueError("the letter result must name its actual descriptor URL")
        if self.letter_info is not None and self.result is not None:
            info, result = self.letter_info, self.result.projection
            if (info.letter_date, info.days, info.email) != (
                result.letter_date,
                result.days,
                result.email,
            ):
                raise ValueError("the letter result must preserve the actual parsed fields")
        if (
            self.date_argument.source == "letter.created"
            and self.date_argument.value != self.official.letter.created
        ):
            raise ValueError("the letter-created fallback must use the descriptor date")
        if self.physical.input_extraction_relation != "unknown" and self.input is None:
            raise ValueError("an exact extraction mapping requires an actual parser input")
        return self

    def omission_reasons(self) -> tuple[OmissionReason, ...]:
        values: list[tuple[OmissionReason, str | None, int]] = [
            ("descriptor_name_bytes", self.official.letter.name, 512),
            ("url_bytes", self.official.letter.url.value, 1024),
            ("error_class_bytes", self.error_class, 128),
            ("python_bytes", self.components.python, 64),
            ("email_bytes", self.letter_info.email if self.letter_info else None, 320),
        ]
        for fact in (self.result, self.before, self.after):
            if fact is not None:
                value = fact.projection
                values.extend(
                    (
                        ("email_bytes", value.email, 320),
                        ("url_bytes", value.letter_url.value if value.letter_url else None, 1024),
                    )
                )
        return tuple(
            sorted(
                {
                    name
                    for name, value, limit in values
                    if value is not None and len(value.encode()) > limit
                }
            )
        )


class RclLetterOmissionOfficial(_Fact):
    project_id: OfficialId
    stage_id: OfficialId | None
    stage_number: SqlInteger
    letter_id: OfficialId
    letter_url_sha256: Sha256


class RclLetterOmissionComponents(_Fact):
    letter_parser: SqlInteger
    parser_sha256: Sha256 | None = None
    merge_sha256: Sha256 | None = None


class RclLetterUseOmission(_Fact):
    contract: Literal["rcl-letter-use-omission-v1"] = "rcl-letter-use-omission-v1"
    scope: Literal["source_parse_in_memory_merge_not_application"] = (
        "source_parse_in_memory_merge_not_application"
    )
    official: RclLetterOmissionOfficial
    physical: RclLetterPhysical
    input: RclLetterInput | None
    components: RclLetterOmissionComponents
    output_sha256: Sha256
    before_sha256: Sha256 | None
    after_sha256: Sha256 | None
    outcome: Literal["returned", "no_text", "read_error", "raised"]
    error_class_sha256: Sha256 | None
    omitted: tuple[OmissionReason, ...] = Field(min_length=1, max_length=6)
    complete_metadata_revalidation: Literal["unavailable"] = "unavailable"

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.omitted != tuple(sorted(set(self.omitted))):
            raise ValueError("omission reasons must be distinct and canonical")
        if (self.error_class_sha256 is not None) != (self.outcome in ("read_error", "raised")):
            raise ValueError("failure class hash and outcome must agree")
        if (self.after_sha256 is None) != (self.outcome == "raised"):
            raise ValueError("omission must preserve whether a merge completed")
        if self.outcome == "returned" and self.input is None:
            raise ValueError("a parsed omission requires the original input identity")
        if self.outcome in ("no_text", "read_error") and self.input is not None:
            raise ValueError("an unparsed omission cannot invent a parser input")
        if self.physical.input_extraction_relation != "unknown" and self.input is None:
            raise ValueError("an exact extraction mapping requires an actual parser input")
        return self


class RclLetterUse(_Fact):
    use_id: str = Field(pattern=r"^rcl-letter-use-v1:[0-9a-f]{64}$")
    semantic_sha256: Sha256
    payload_state: Literal["complete", "omitted_over_limit"]
    payload: Annotated[RclLetterUsePayload | RclLetterUseOmission, Field(discriminator="contract")]

    @classmethod
    def of(cls, payload: RclLetterUsePayload) -> Self:
        payload = RclLetterUsePayload.model_validate_json(payload.model_dump_json())
        values = payload.model_dump(mode="json")
        semantic = rcl_letter_sha256(values)
        base = {"use_id": "rcl-letter-use-v1:" + semantic, "semantic_sha256": semantic}
        reasons = set(payload.omission_reasons())
        estimate = {
            **base,
            "payload_state": "complete",
            "payload": values,
            "recorded_at": _MAX_TIME.isoformat(),
        }
        if len(rcl_letter_canonical(estimate)) > MAX_RCL_LETTER_USE_BYTES:
            reasons.add("record_bytes")
        if not reasons:
            return cls(**base, payload_state="complete", payload=payload)
        omitted = RclLetterUseOmission(
            official=RclLetterOmissionOfficial.model_validate(
                {
                    **payload.official.model_dump(exclude={"letter"}),
                    "letter_url_sha256": payload.official.letter.url.sha256,
                }
            ),
            physical=payload.physical,
            input=payload.input,
            components=RclLetterOmissionComponents.model_validate(
                payload.components.model_dump(exclude={"python"})
            ),
            output_sha256=rcl_letter_sha256(
                {key: values[key] for key in ("letter_info", "result")}
            ),
            before_sha256=payload.before.canonical_projection_sha256 if payload.before else None,
            after_sha256=payload.after.canonical_projection_sha256 if payload.after else None,
            outcome=payload.outcome,
            error_class_sha256=hashlib.sha256(payload.error_class.encode()).hexdigest()
            if payload.error_class is not None
            else None,
            omitted=tuple(sorted(reasons)),
        )
        return cls(**base, payload_state="omitted_over_limit", payload=omitted)

    @model_validator(mode="after")
    def identity_and_bound(self) -> Self:
        if self.use_id != "rcl-letter-use-v1:" + self.semantic_sha256:
            raise ValueError("the use ID must match its semantic hash")
        complete = isinstance(self.payload, RclLetterUsePayload)
        if complete != (self.payload_state == "complete"):
            raise ValueError("the retained payload and completeness state must agree")
        if isinstance(self.payload, RclLetterUsePayload) and (
            self.payload.omission_reasons()
            or rcl_letter_sha256(self.payload.model_dump(mode="json")) != self.semantic_sha256
        ):
            raise ValueError("complete letter-use metadata must reproduce its bounded identity")
        data = {**self.model_dump(mode="json"), "recorded_at": _MAX_TIME.isoformat()}
        maximum = MAX_RCL_LETTER_USE_BYTES if complete else MAX_RCL_LETTER_OMISSION_BYTES
        if len(rcl_letter_canonical(data)) > maximum:
            raise ValueError("letter-use metadata exceeds its serialized byte limit")
        return self

    def verify_parents(
        self,
        artifact: ArtifactIdentity | None,
        read: ArtifactReadIdentity | None,
        extraction: ExtractionResult | None,
    ) -> None:
        physical = self.payload.physical
        if physical.artifact_sha256 is None:
            if artifact is not None or read is not None or extraction is not None:
                raise ValueError("unknown letter origin cannot accept unrelated parents")
            return
        if artifact is None or (artifact.sha256, artifact.byte_size) != (
            physical.artifact_sha256,
            physical.artifact_bytes,
        ):
            raise ValueError("the letter body requires matching complete metadata")
        if (read is None) != (physical.artifact_read_id is None):
            raise ValueError("the letter read reference must have its exact parent")
        if read is not None:
            if (read.read_id, read.sha256, read.byte_size) != (
                physical.artifact_read_id,
                artifact.sha256,
                artifact.byte_size,
            ):
                raise ValueError("the letter read parent must match its body identity")
            requested = (
                self.payload.official.letter.url.sha256
                if isinstance(self.payload, RclLetterUsePayload)
                else self.payload.official.letter_url_sha256
            )
            if hashlib.sha256(read.requested_url.encode()).hexdigest() != requested:
                raise ValueError("the letter read parent must match its exact requested URL")
        if (extraction is None) != (physical.extraction_id is None):
            raise ValueError("the letter extraction reference must have its exact parent")
        if extraction is not None:
            if (extraction.result_id, extraction.artifact_sha256) != (
                physical.extraction_id,
                artifact.sha256,
            ):
                raise ValueError("the letter extraction parent must match its exact outer body")
            if physical.input_extraction_relation != "unknown" and (
                self.payload.input is None
                or self.payload.input.sha256 != extraction.output_text_sha256
                or self.payload.input.chars != extraction.output_chars
            ):
                raise ValueError("exact parser/extraction mapping requires equal input identity")


class RclLetterUseRecord(RclLetterUse):
    recorded_at: dt.datetime

    @field_validator("recorded_at")
    @classmethod
    def aware_utc(cls, value: dt.datetime) -> dt.datetime:
        if value.utcoffset() is None:
            raise ValueError("letter-use recording time requires a timezone")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def recording_time(self) -> Self:
        maximum = (
            MAX_RCL_LETTER_USE_BYTES
            if self.payload_state == "complete"
            else MAX_RCL_LETTER_OMISSION_BYTES
        )
        if len(rcl_letter_canonical(self.model_dump(mode="json"))) > maximum:
            raise ValueError("the persisted letter-use record exceeds its byte limit")
        return self


class RclLetterUseObservation(_Fact):
    use: RclLetterUse
    artifact: ArtifactIdentity | None = None
    read: ArtifactReadIdentity | None = None
    extraction: ExtractionResult | None = None

    @model_validator(mode="after")
    def parents(self) -> Self:
        self.use.verify_parents(self.artifact, self.read, self.extraction)
        return self
