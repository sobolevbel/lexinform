"""Physical metadata preserves observed identities independently of extraction and selection."""

import datetime as dt
import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_PHYSICAL_METADATA_BYTES = 32_768
MAX_PHYSICAL_CAPTURE_BYTES = 1024
MAX_PHYSICAL_ROOT_BYTES = 524_288
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ExtractionId = Annotated[str, Field(pattern=r"^extraction-v1:[0-9a-f]{64}$")]
Position = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Flag = Annotated[bool, Field(strict=True)]
ErrorName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")]
MetadataKind = Literal["headers", "graph", "unit", "execution", "recipe"]
OpaqueBytes = tuple[Position, Sha256 | None]
DecodedPath = (
    tuple[Literal["unicode_path"], Position, Position, Position, Flag, Flag, Flag, Sha256]
    | tuple[Literal["unicode_path_short", "short_tail"], Position, Position]
    | tuple[Literal["malformed_tail"], Position, Position, Position]
)
HeaderRow = tuple[
    str,
    str | None,
    Position,
    Position,
    Position,
    Position,
    Position,
    Position,
    Position,
    Position,
    OpaqueBytes,
    OpaqueBytes,
    tuple[DecodedPath, ...],
]
GraphEdge = tuple[Sha256 | None, Sha256 | None]
ReadRequest = tuple[
    Literal["member", "xml"],
    Literal["ordinal", "name"],
    Position | str,
    Position | None,
    Literal["complete", "failed", "returned_length_differs"],
    Position | None,
    Sha256 | None,
    ErrorName | None,
]
PayloadOpen = tuple[
    Position | None,
    Literal["ordinal", "name"],
    Position | str,
    Position | None,
    Literal["opened", "failed"],
    ErrorName | None,
]
HeaderDisposition = Literal[
    "uninspected_format_part",
    "readability_rejected",
    "member_complete",
    "member_failed",
    "member_returned_length_differs",
    "unpacking_limit_source_pinned_inference",
    "not_visited_depth_limit",
    "format_complete",
    "format_failed",
    "format_returned_length_differs",
]
RootContext = tuple[Position | None, Position | None, Position | None, Sha256]
OrderSlot = tuple[Position, Position]
PhysicalSlot = tuple[Position, Sha256, Sha256]
OmittedField = Literal[
    "rows",
    "edges",
    "statuses",
    "requests",
    "opens",
    "contexts",
    "physical",
    "zip_failures",
    "code_files",
    "implementations",
    "dependencies",
]
_MAX_TIME = dt.datetime.max.replace(tzinfo=dt.UTC)


def physical_canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def physical_sha256(value: object) -> str:
    return hashlib.sha256(physical_canonical(value)).hexdigest()


def _timestamp(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.utcoffset() is None:
        raise ValueError("physical recording time requires a timezone")
    try:
        return value.astimezone(dt.UTC)
    except OverflowError as exc:
        raise ValueError("physical recording time is outside UTC datetime range") from exc


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class PhysicalHeaders(_Fact):
    v: Literal[1] = 1
    body_sha: Sha256
    central_directory_sha256: Sha256
    central_directory_span: tuple[Position, Position]
    archive_comment: OpaqueBytes
    rows: tuple[HeaderRow, ...]

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.central_directory_span[0] > self.central_directory_span[1]:
            raise ValueError("central directory span is reversed")
        for length, digest in (
            self.archive_comment,
            *(part for row in self.rows for part in row[10:12]),
        ):
            if (length == 0) != (digest is None):
                raise ValueError("opaque header bytes require exact length and known nonempty hash")
        return self


class PhysicalGraph(_Fact):
    v: Literal[1] = 1
    body_sha: Sha256
    header: Sha256 | None
    edges: tuple[GraphEdge, ...]

    @model_validator(mode="after")
    def identities(self) -> Self:
        if self.header is None and self.edges:
            raise ValueError("member edges require an observed header manifest")
        if any(body is None and child is not None for body, child in self.edges):
            raise ValueError("a child subgraph requires its known complete payload hash")
        return self


class PhysicalContainerExecution(_Fact):
    v: Literal[1] = 1
    graph: Sha256
    route: Literal["archive", "word", "odt", "non_zip", "zip_unreadable"]
    propagated_error: ErrorName | None
    format_error: ErrorName | None
    statuses: tuple[HeaderDisposition, ...]
    requests: tuple[ReadRequest, ...]
    opens: tuple[PayloadOpen, ...]

    @model_validator(mode="after")
    def outcomes(self) -> Self:
        for mode, form, requested, _, outcome, size, body, error in self.requests:
            if (mode == "member") != (form == "ordinal"):
                raise ValueError("member and format requests retain their actual request form")
            if (form == "ordinal") != isinstance(requested, int):
                raise ValueError("ordinal and name requests must retain distinct identities")
            if outcome == "complete" and (size is None or body is None or error is not None):
                raise ValueError("complete reads require their known full byte identity")
            if outcome == "failed" and (size is not None or body is not None or error is None):
                raise ValueError("failed reads cannot invent returned complete-body identities")
            if outcome == "returned_length_differs" and (
                size is None or body is not None or error is not None
            ):
                raise ValueError("length-mismatched returns retain no complete-body hash")
        for _, form, requested, resolved, open_outcome, error in self.opens:
            if (form == "ordinal") != isinstance(requested, int):
                raise ValueError("opened requests retain ordinal versus name form")
            if open_outcome == "opened" and (resolved is None or error is not None):
                raise ValueError("opened payloads require the actual resolved occurrence")
            if open_outcome == "failed" and error is None:
                raise ValueError("failed opens require a safe error class")
        return self


class PhysicalRootOrder(_Fact):
    v: Literal[1] = 1
    contexts: tuple[RootContext, ...]
    requests: tuple[OrderSlot, ...]
    opens: tuple[OrderSlot, ...]
    physical: tuple[PhysicalSlot, ...]
    zip_failures: tuple[tuple[Position, ErrorName], ...]
    legacy_rejections: tuple[Position, Sha256]


class PhysicalCodeFile(_Fact):
    path: str = Field(min_length=1, max_length=512)
    sha256: Sha256 | None


class PhysicalImplementation(_Fact):
    role: Literal["package", "pdf", "word", "doc", "odt", "capture"]
    qualified_name: str = Field(min_length=1, max_length=256)
    code_path: str = Field(min_length=1, max_length=512)


class PhysicalDependency(_Fact):
    name: Literal["python", "pypdf", "olefile", "inflate64", "pydantic", "pydantic_core"]
    version: str | None = Field(max_length=64)


class PhysicalCaptureOptions(_Fact):
    max_root_bytes: Position | None
    max_member_bytes: Position | None
    max_part_chars: Position | None
    archive_depth: Position
    archive_bytes: Position
    archive_members: Position
    xml_bytes: Position
    include_pdf_pages: Flag
    include_word_blocks: Flag
    include_word_notes: Flag
    include_odt_blocks: Flag
    include_unlocated_text: Flag


class PhysicalCaptureRecipe(_Fact):
    v: Literal[1] = 1
    contract: Literal["physical-inventory-v1"] = "physical-inventory-v1"
    mode: Literal["observer", "producer"]
    code_files: tuple[PhysicalCodeFile, ...]
    implementations: tuple[PhysicalImplementation, ...]
    dependencies: tuple[PhysicalDependency, ...]
    options: PhysicalCaptureOptions

    @model_validator(mode="after")
    def uniqueness(self) -> Self:
        for keys in (
            tuple(item.path for item in self.code_files),
            tuple(item.role for item in self.implementations),
            tuple(item.name for item in self.dependencies),
        ):
            if len(keys) != len(set(keys)):
                raise ValueError("recipe code, role and dependency identities must be unique")
        return self


class PhysicalOmittedCount(_Fact):
    field: OmittedField
    count: Position = Field(gt=0)


class PhysicalOmission(_Fact):
    reason: Literal["over_limit", "missing_metadata"]
    full_metadata_sha256: Sha256 | None
    omitted: tuple[PhysicalOmittedCount, ...] = ()
    unknown_fields: Position = 0

    @model_validator(mode="after")
    def identity(self) -> Self:
        if not self.omitted and not self.unknown_fields:
            raise ValueError("partial metadata requires explicit omissions or unknown fields")
        if len(self.omitted) != len({item.field for item in self.omitted}):
            raise ValueError("omission counters must be unique")
        if self.reason == "over_limit" and self.full_metadata_sha256 is None:
            raise ValueError("overflow requires the opaque full observed metadata hash")
        return self


PhysicalPayload = (
    PhysicalHeaders
    | PhysicalGraph
    | PhysicalContainerExecution
    | PhysicalRootOrder
    | PhysicalCaptureRecipe
)


def physical_kind(payload: PhysicalPayload) -> MetadataKind:
    if isinstance(payload, PhysicalHeaders):
        return "headers"
    if isinstance(payload, PhysicalGraph):
        return "graph"
    if isinstance(payload, PhysicalContainerExecution):
        return "unit"
    if isinstance(payload, PhysicalRootOrder):
        return "execution"
    return "recipe"


class PhysicalMetadata(_Fact):
    metadata_id: Sha256
    kind: MetadataKind
    scope: Literal["observed"] = "observed"
    body_sha256: Sha256 | None
    header_id: Sha256 | None
    metadata: PhysicalPayload
    coverage: Literal["complete", "partial"] = "complete"
    omission: PhysicalOmission | None = None
    recorded_at: dt.datetime | None = None

    _time = field_validator("recorded_at")(_timestamp)

    @classmethod
    def of(
        cls,
        metadata: PhysicalPayload,
        *,
        body_sha256: str | None = None,
        header_id: str | None = None,
        omission: PhysicalOmission | None = None,
    ) -> Self:
        kind = physical_kind(metadata)
        if isinstance(metadata, (PhysicalHeaders, PhysicalGraph)):
            body_sha256 = metadata.body_sha
            header_id = metadata.header if isinstance(metadata, PhysicalGraph) else None
        if isinstance(metadata, PhysicalCaptureRecipe):
            body_sha256, header_id = None, None
        if isinstance(metadata, PhysicalRootOrder):
            header_id = None
        value = metadata.model_dump(mode="json")
        identity = physical_sha256(
            value
            if omission is None
            else {
                "kind": kind,
                "body_sha256": body_sha256,
                "header_id": header_id,
                "retained": value,
                "omission": omission.model_dump(mode="json"),
            }
        )
        return cls(
            metadata_id=identity,
            kind=kind,
            body_sha256=body_sha256,
            header_id=header_id,
            metadata=metadata,
            coverage="partial" if omission is not None else "complete",
            omission=omission,
        )

    def omitted(self, field: OmittedField) -> int:
        return (
            next((item.count for item in self.omission.omitted if item.field == field), 0)
            if self.omission
            else 0
        )

    def total(self, field: OmittedField, retained: int) -> int:
        return retained + self.omitted(field)

    @property
    def budget_bytes(self) -> int:
        value = self.model_dump(mode="json")
        value["recorded_at"] = (self.recorded_at or _MAX_TIME).isoformat()
        return len(physical_canonical(value))

    @model_validator(mode="after")
    def envelope(self) -> Self:
        if self.kind != physical_kind(self.metadata):
            raise ValueError("physical kind differs from its closed record payload")
        if (self.coverage == "partial") != (self.omission is not None):
            raise ValueError("partial coverage requires its retained-field omission identity")
        allowed: set[OmittedField]
        if self.kind == "headers":
            allowed = {"rows"}
        elif self.kind == "graph":
            allowed = {"edges"}
        elif self.kind == "unit":
            allowed = {"statuses", "requests", "opens"}
        elif self.kind == "execution":
            allowed = {"contexts", "requests", "opens", "physical", "zip_failures"}
        else:
            allowed = {"code_files", "implementations", "dependencies"}
        if self.omission is not None and any(
            item.field not in allowed for item in self.omission.omitted
        ):
            raise ValueError("omission counters differ from their actual physical record kind")
        if self.kind == "recipe":
            if self.body_sha256 is not None or self.header_id is not None:
                raise ValueError("capture recipes are independent of artifact ownership")
        elif self.body_sha256 is None:
            raise ValueError("physical records require their known complete input body")
        if isinstance(self.metadata, (PhysicalHeaders, PhysicalGraph)):
            expected_header = (
                self.metadata.header if isinstance(self.metadata, PhysicalGraph) else None
            )
            if (self.body_sha256, self.header_id) != (self.metadata.body_sha, expected_header):
                raise ValueError("physical envelope differs from its retained body/header identity")
        if isinstance(self.metadata, PhysicalRootOrder) and self.header_id is not None:
            raise ValueError("root order references its headers through actual container contexts")
        raw = self.metadata.model_dump(mode="json")
        identity = physical_sha256(
            raw
            if self.omission is None
            else {
                "kind": self.kind,
                "body_sha256": self.body_sha256,
                "header_id": self.header_id,
                "retained": raw,
                "omission": self.omission.model_dump(mode="json"),
            }
        )
        if identity != self.metadata_id:
            raise ValueError("physical metadata hash differs from its retained fields")
        if self.coverage == "complete":
            if isinstance(self.metadata, PhysicalRootOrder) and not self.metadata.contexts:
                raise ValueError("complete root order requires its actual root context")
            if isinstance(self.metadata, PhysicalCaptureRecipe):
                recipe = self.metadata
                if (
                    not recipe.code_files
                    or any(item.sha256 is None for item in recipe.code_files)
                    or {item.role for item in recipe.implementations}
                    != {"package", "pdf", "word", "doc", "odt", "capture"}
                    or {item.name for item in recipe.dependencies}
                    != {"python", "pypdf", "olefile", "inflate64", "pydantic", "pydantic_core"}
                    or any(item.version is None for item in recipe.dependencies)
                    or any(
                        item.code_path not in {code.path for code in recipe.code_files}
                        for item in recipe.implementations
                    )
                ):
                    raise ValueError(
                        "complete capture recipes require reproducible code and dependencies"
                    )
        if self.budget_bytes > MAX_PHYSICAL_METADATA_BYTES:
            raise ValueError("physical metadata exceeds its complete envelope byte budget")
        return self


class PhysicalCapture(_Fact):
    capture_id: Sha256
    version: Literal[1] = 1
    scope: Literal["observed"] = "observed"
    artifact_sha256: Sha256
    recipe_id: Sha256
    inventory_id: Sha256
    execution_id: Sha256
    extraction_id: ExtractionId | None
    recorded_at: dt.datetime | None = None

    _time = field_validator("recorded_at")(_timestamp)

    @classmethod
    def of(
        cls,
        *,
        artifact_sha256: str,
        recipe_id: str,
        inventory_id: str,
        execution_id: str,
        extraction_id: str | None = None,
    ) -> Self:
        value = {
            "version": 1,
            "scope": "observed",
            "artifact_sha256": artifact_sha256,
            "recipe_id": recipe_id,
            "inventory_id": inventory_id,
            "execution_id": execution_id,
            "extraction_id": extraction_id,
        }
        return cls(
            capture_id=physical_sha256(value),
            artifact_sha256=artifact_sha256,
            recipe_id=recipe_id,
            inventory_id=inventory_id,
            execution_id=execution_id,
            extraction_id=extraction_id,
        )

    @property
    def budget_bytes(self) -> int:
        value = self.model_dump(mode="json")
        value["recorded_at"] = (self.recorded_at or _MAX_TIME).isoformat()
        return len(physical_canonical(value))

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.capture_id != physical_sha256(
            self.model_dump(mode="json", exclude={"capture_id", "recorded_at"})
        ):
            raise ValueError(
                "physical capture identity differs from its actual roots and association"
            )
        if self.budget_bytes > MAX_PHYSICAL_CAPTURE_BYTES:
            raise ValueError("physical capture exceeds its envelope byte budget")
        return self
