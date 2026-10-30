"""Assemble observed physical facts without replaying extraction, policy or persistence."""

import json
from dataclasses import dataclass, replace
from itertools import chain
from typing import Literal

from lexinform.models.artifacts import ArtifactIdentity
from lexinform.models.extractions import ExtractionResult
from lexinform.models.physical_inventory import (
    MAX_PHYSICAL_METADATA_BYTES,
    MAX_PHYSICAL_ROOT_BYTES,
    MetadataKind,
    OmittedField,
    PhysicalCapture,
    PhysicalCaptureRecipe,
    PhysicalContainerExecution,
    PhysicalGraph,
    PhysicalHeaders,
    PhysicalMetadata,
    PhysicalOmission,
    PhysicalRootOrder,
    RootContext,
    physical_kind,
)
from lexinform.physical_assembly_encoding import (
    physical_encoded_size,
    physical_record_size,
    validate_physical_record,
)
from lexinform.physical_assembly_encoding import (
    physical_observed_hash as _observed_hash,
)
from lexinform.physical_assembly_inputs import (
    ObservedPhysicalContainer as ObservedPhysicalContainer,
)
from lexinform.physical_assembly_inputs import ObservedPhysicalOrder as ObservedPhysicalOrder
from lexinform.physical_assembly_integrity import (
    PhysicalAssemblyIntegrity,
    PhysicalAssemblyProofError,
    physical_identity,
)
from lexinform.physical_assembly_integrity import (
    validate_position as _position,
)


@dataclass(frozen=True)
class PhysicalAssemblyPacket:
    artifacts: tuple[ArtifactIdentity, ...]
    metadata: tuple[PhysicalMetadata, ...]
    capture: PhysicalCapture


@dataclass(frozen=True)
class PhysicalAssemblyGap:
    artifact: ArtifactIdentity
    reason: Literal[
        "record_envelope_over_limit",
        "root_union_over_limit",
        "invalid_observed_facts",
        "missing_root_observation",
        "header_universe_unavailable",
        "parent_observation_unavailable",
        "integrity_proof_capacity",
    ]
    kind: MetadataKind
    distinct_addresses: int
    full_metadata_sha256: str | None


class PhysicalRootAssembler:
    def __init__(self, artifact: ArtifactIdentity) -> None:
        self.artifact = physical_identity(artifact)
        self._integrity = PhysicalAssemblyIntegrity(self.artifact)
        self._records: dict[str, PhysicalMetadata] = {}
        self._bodies: dict[str, ArtifactIdentity] = {artifact.sha256: self.artifact}
        self._contexts: dict[int, RootContext] = {}
        self._units: dict[int, PhysicalContainerExecution] = {}
        self._graphs: dict[int, PhysicalMetadata] = {}
        self._children: dict[int, dict[int, tuple[str, str]]] = {}
        self._bytes = 0
        self._context_bytes = 0
        self._gap: PhysicalAssemblyGap | None = None
        self._closed = False
        self._overflow = False
        self._root_seen = False
        self._root_context_complete = False
        self._root_header: PhysicalMetadata | None = None
        self._root_graph_hash: str | None = None
        self._root_order_hash: str | None = None

    def _body(self, value: ArtifactIdentity) -> ArtifactIdentity:
        value = physical_identity(value)
        previous = self._bodies.get(value.sha256)
        if previous is not None and previous != value:
            raise ValueError("an observed complete body cannot have conflicting byte sizes")
        self._bodies[value.sha256] = value
        return value

    def _fail(
        self,
        reason: Literal[
            "record_envelope_over_limit",
            "root_union_over_limit",
            "invalid_observed_facts",
            "missing_root_observation",
            "header_universe_unavailable",
            "parent_observation_unavailable",
            "integrity_proof_capacity",
        ],
        kind: MetadataKind,
        full_hash: str | None,
    ) -> None:
        if reason in ("record_envelope_over_limit", "root_union_over_limit"):
            self._overflow = True
        else:
            self._gap = PhysicalAssemblyGap(
                self.artifact, reason, kind, self._seen_contexts, full_hash
            )
        self._records.clear()
        self._bodies = {self.artifact.sha256: self.artifact}
        self._contexts.clear()
        self._units.clear()
        self._graphs.clear()
        self._children.clear()
        self._bytes = 0

    def _store(self, record: PhysicalMetadata) -> bool:
        pending = record.model_copy(update={"recorded_at": None})
        pending_size = physical_record_size(pending)
        if pending_size is None:
            full_hash = (
                record.omission.full_metadata_sha256
                if record.omission is not None
                else _observed_hash(record.metadata)
            )
            self._fail("record_envelope_over_limit", record.kind, full_hash)
            return False
        record = PhysicalMetadata.model_validate_json(pending.model_dump_json())
        previous = self._records.get(record.metadata_id)
        if previous is not None:
            if previous != record:
                raise ValueError("a physical content identity cannot replace retained metadata")
            return True
        if self._bytes + pending_size > MAX_PHYSICAL_ROOT_BYTES:
            self._fail("root_union_over_limit", record.kind, None)
            return False
        self._records[record.metadata_id] = record
        self._bytes += pending_size
        return True

    def _emit(
        self,
        payload: PhysicalGraph | PhysicalContainerExecution | PhysicalRootOrder,
        *,
        body: str | None = None,
        header: str | None = None,
        complete: bool = True,
    ) -> PhysicalMetadata | None:
        omission = (
            None
            if complete
            else PhysicalOmission(
                reason="missing_metadata", full_metadata_sha256=None, unknown_fields=1
            )
        )
        if isinstance(payload, PhysicalGraph):
            body, header = payload.body_sha, payload.header
        elif isinstance(payload, PhysicalRootOrder):
            self._root_order_hash = _observed_hash(payload) if complete else None
        bounded = PhysicalMetadata.model_construct(
            metadata_id="f" * 64,
            kind=physical_kind(payload),
            body_sha256=body,
            header_id=header,
            metadata=payload,
            coverage="complete" if complete else "partial",
            omission=omission,
        )
        if physical_record_size(bounded) is None:
            self._fail(
                "record_envelope_over_limit",
                physical_kind(payload),
                _observed_hash(payload) if complete else None,
            )
            return None
        record = PhysicalMetadata.of(payload, body_sha256=body, header_id=header, omission=omission)
        return self._records[record.metadata_id] if self._store(record) else None

    def _required_gap(self) -> PhysicalAssemblyGap:
        gap = self._gap
        if gap is None:
            raise ValueError("physical representation failure requires its explicit gap")
        return gap

    def add_container(self, observed: ObservedPhysicalContainer) -> None:
        if self._closed:
            raise ValueError("closed physical assembly cannot accept another occurrence")
        if self._gap is not None:
            return
        try:
            header = self._integrity.observe(observed)
            observed = replace(observed, header=header)
            if self._root_seen and observed.index != 0:
                raise ValueError("child contexts must finalize before their actual root")
            if type(observed.complete) is not bool:
                raise ValueError("physical observation completeness requires an actual boolean")
            if observed.index == 0:
                self._remember_root(observed)
            if not self._overflow:
                if not self._context_fits(observed):
                    self._fail("record_envelope_over_limit", "graph", None)
                else:
                    self._add_container(observed)
        except PhysicalAssemblyProofError as exc:
            self._fail(exc.reason, "execution", None)
            raise
        except BaseException:
            self._fail("invalid_observed_facts", "execution", None)
            raise

    @property
    def _seen_contexts(self) -> int:
        return self._integrity.context_count

    def _context_fits(self, observed: ObservedPhysicalContainer) -> bool:
        header = observed.header
        count = (
            header.total("rows", len(header.metadata.rows))
            if header is not None and isinstance(header.metadata, PhysicalHeaders)
            else 0
        )
        if count > MAX_PHYSICAL_METADATA_BYTES // 12 or len(observed.statuses) != count:
            return False
        edge_bytes = max(0, 12 * count - 1) + 62 * (
            len(observed.known_members) + len(self._children.get(observed.index, {}))
        )
        if edge_bytes > MAX_PHYSICAL_METADATA_BYTES - 512:
            return False
        unit = {
            "v": 1,
            "graph": "f" * 64,
            "route": observed.route,
            "propagated_error": observed.propagated_error,
            "format_error": observed.format_error,
            "statuses": observed.statuses,
            "requests": observed.requests,
            "opens": observed.opens,
        }
        return physical_encoded_size(unit, MAX_PHYSICAL_METADATA_BYTES - 512) is not None

    def _add_container(self, observed: ObservedPhysicalContainer) -> None:
        if observed.index in self._contexts:
            raise ValueError("physical occurrence index was already finalized")
        if observed.parent is not None and observed.parent in self._contexts:
            raise ValueError("parents finalize after their actual child contexts")
        body = self._body(observed.body)
        header = observed.header
        count = 0
        if header is not None:
            if (
                not isinstance(header.metadata, PhysicalHeaders)
                or header.body_sha256 != body.sha256
            ):
                raise ValueError("container header must identify its actual body")
            count = header.total("rows", len(header.metadata.rows))
            if header.metadata.central_directory_span[1] > body.byte_size:
                raise ValueError("header directory span exceeds its known complete body size")
            if not self._store(header):
                return
        members: dict[int, ArtifactIdentity] = {}
        for ordinal, member in observed.known_members:
            _position(ordinal)
            if ordinal >= count or ordinal in members:
                raise ValueError(
                    "known member occurrence differs from the observed header universe"
                )
            members[ordinal] = self._body(member)
        children = self._children.pop(observed.index, {})
        for ordinal, (child_body, _) in children.items():
            if ordinal not in members or members[ordinal].sha256 != child_body:
                raise ValueError("child graph requires its exact observed complete member body")
        graph_payload = PhysicalGraph(
            body_sha=body.sha256,
            header=header.metadata_id if header else None,
            edges=tuple(
                (
                    members[ordinal].sha256 if ordinal in members else None,
                    children[ordinal][1] if ordinal in children else None,
                )
                for ordinal in range(count)
            ),
        )
        if observed.index == 0:
            self._root_graph_hash = _observed_hash(graph_payload) if observed.complete else None
        graph = self._emit(graph_payload, complete=observed.complete)
        if graph is None:
            return
        unit_payload = PhysicalContainerExecution(
            graph=graph.metadata_id,
            route=observed.route,
            propagated_error=observed.propagated_error,
            format_error=observed.format_error,
            statuses=observed.statuses,
            requests=observed.requests,
            opens=observed.opens,
        )
        if len(observed.statuses) != count:
            raise ValueError(
                "actual container dispositions must cover its observed header universe"
            )
        unit = self._emit(
            unit_payload, body=body.sha256, header=graph.header_id, complete=observed.complete
        )
        if unit is None:
            return
        context: RootContext = (
            observed.parent,
            observed.parent_ordinal,
            observed.physical_index,
            unit.metadata_id,
        )
        self._context_bytes += len(json.dumps(context, separators=(",", ":")).encode()) + bool(
            self._contexts
        )
        if self._context_bytes > MAX_PHYSICAL_METADATA_BYTES:
            self._fail("record_envelope_over_limit", "execution", None)
            return
        self._contexts[observed.index] = context
        assert isinstance(unit.metadata, PhysicalContainerExecution), (
            "unit emission preserves its closed payload kind"
        )
        self._units[observed.index] = unit.metadata
        self._graphs[observed.index] = graph
        if observed.parent is not None:
            assert observed.parent_ordinal is not None, "parent and ordinal remain paired"
            siblings = self._children.setdefault(observed.parent, {})
            if observed.parent_ordinal in siblings:
                raise ValueError("an actual member occurrence cannot acquire two child contexts")
            siblings[observed.parent_ordinal] = body.sha256, graph.metadata_id

    def finish(
        self,
        recipe: PhysicalMetadata,
        order: ObservedPhysicalOrder,
        *,
        complete: bool,
        extraction: ExtractionResult | None = None,
    ) -> PhysicalAssemblyPacket | PhysicalAssemblyGap:
        if self._closed:
            raise ValueError("physical root assembly can be finalized only once")
        if type(complete) is not bool:
            raise ValueError("physical root completeness requires an actual boolean")
        self._closed = True
        if self._gap is not None:
            return PhysicalAssemblyGap(
                self.artifact,
                self._gap.reason,
                self._gap.kind,
                self._seen_contexts,
                self._gap.full_metadata_sha256,
            )
        try:
            self._integrity.verify_order(order)
        except PhysicalAssemblyProofError as exc:
            self._fail(exc.reason, "execution", None)
            return self._required_gap()
        if self._overflow:
            return self._minimum_packet(recipe, order, complete=complete, extraction=extraction)
        if self._children or sorted(self._contexts) != list(range(len(self._contexts))):
            raise ValueError("root packet requires all actually observed context parents and slots")
        if not self._contexts or 0 not in self._graphs:
            if not complete:
                self._fail("missing_root_observation", "execution", None)
                return self._required_gap()
            raise ValueError("root packet requires its actual observed root context")
        recipe = validate_physical_record(recipe)
        if recipe.kind != "recipe":
            raise ValueError("physical capture requires its supplied actual recipe")
        if not self._store(recipe):
            return self._minimum_packet(recipe, order, complete=complete, extraction=extraction)
        contexts = tuple(self._contexts[index] for index in range(len(self._contexts)))
        fields = {
            "v": 1,
            "contexts": contexts,
            "requests": order.requests,
            "opens": order.opens,
            "physical": order.physical,
            "zip_failures": order.zip_failures,
            "legacy_rejections": order.legacy_rejections,
        }
        if physical_encoded_size(fields, MAX_PHYSICAL_METADATA_BYTES - 512) is None:
            self._root_order_hash = _observed_hash(fields) if complete else None
            self._fail("record_envelope_over_limit", "execution", None)
            return self._minimum_packet(recipe, order, complete=complete, extraction=extraction)
        root = self._emit(
            PhysicalRootOrder(
                contexts=contexts,
                requests=order.requests,
                opens=order.opens,
                physical=order.physical,
                zip_failures=order.zip_failures,
                legacy_rejections=order.legacy_rejections,
            ),
            body=self.artifact.sha256,
            complete=complete,
        )
        if root is None:
            return self._minimum_packet(recipe, order, complete=complete, extraction=extraction)
        if extraction is not None:
            extraction = ExtractionResult.model_validate_json(extraction.model_dump_json())
            if extraction.artifact_sha256 != self.artifact.sha256:
                raise ValueError("a physical capture cannot borrow another root's extraction")
        capture = PhysicalCapture.of(
            artifact_sha256=self.artifact.sha256,
            recipe_id=recipe.metadata_id,
            inventory_id=self._graphs[0].metadata_id,
            execution_id=root.metadata_id,
            extraction_id=extraction.result_id if extraction else None,
        )
        if self._bytes + capture.budget_bytes > MAX_PHYSICAL_ROOT_BYTES:
            self._fail("root_union_over_limit", "execution", None)
            return self._minimum_packet(recipe, order, complete=complete, extraction=extraction)
        return PhysicalAssemblyPacket(
            tuple(self._bodies[key] for key in sorted(self._bodies)),
            tuple(self._records[key] for key in sorted(self._records)),
            capture,
        )

    def _remember_root(self, observed: ObservedPhysicalContainer) -> None:
        if self._root_seen:
            raise ValueError("actual root occurrence can finalize only once")
        if (
            observed.body != self.artifact
            or observed.parent is not None
            or observed.parent_ordinal is not None
        ):
            raise ValueError("the actual root context must own its complete root body")
        header = observed.header
        if header is not None:
            header = validate_physical_record(header)
            if (
                not isinstance(header.metadata, PhysicalHeaders)
                or header.body_sha256 != self.artifact.sha256
            ):
                raise ValueError("root header must identify its actual complete body")
            if header.metadata.central_directory_span[1] > self.artifact.byte_size:
                raise ValueError("root directory span exceeds its actual complete body")
        self._root_header = header
        self._root_context_complete = observed.complete
        self._root_seen = True

    @staticmethod
    def _omission(
        counts: tuple[tuple[str, int], ...], *, complete: bool, full_hash: str | None
    ) -> PhysicalOmission:
        return PhysicalOmission.model_validate(
            {
                "reason": "over_limit"
                if complete and full_hash is not None
                else "missing_metadata",
                "full_metadata_sha256": full_hash if complete else None,
                "omitted": [{"field": field, "count": count} for field, count in counts if count],
                "unknown_fields": 0 if complete else 1,
            }
        )

    @staticmethod
    def _minimum_record(record: PhysicalMetadata) -> PhysicalMetadata:
        pending = record.model_copy(update={"recorded_at": None})
        if physical_record_size(pending) is not None:
            return PhysicalMetadata.model_validate_json(pending.model_dump_json())
        payload = record.metadata
        fields: tuple[OmittedField, ...]
        if isinstance(payload, PhysicalHeaders):
            fields = ("rows",)
        elif isinstance(payload, PhysicalCaptureRecipe):
            fields = ("code_files", "implementations", "dependencies")
        else:
            raise ValueError("minimum rooted prefix retains only its actual header and recipe")
        counts = tuple(
            (field, record.total(field, len(getattr(payload, field)))) for field in fields
        )
        previous = record.omission
        full_hash = previous.full_metadata_sha256 if previous else _observed_hash(payload)
        omission = PhysicalOmission.model_validate(
            {
                "reason": "over_limit" if full_hash else "missing_metadata",
                "full_metadata_sha256": full_hash,
                "omitted": [{"field": field, "count": count} for field, count in counts if count],
                "unknown_fields": previous.unknown_fields if previous else 0,
            }
        )
        return PhysicalMetadata.of(
            payload.model_copy(update=dict.fromkeys(fields, ())), omission=omission
        )

    def _minimum_packet(
        self,
        recipe: PhysicalMetadata,
        order: ObservedPhysicalOrder,
        *,
        complete: bool,
        extraction: ExtractionResult | None,
    ) -> PhysicalAssemblyPacket | PhysicalAssemblyGap:
        if not self._root_seen:
            return PhysicalAssemblyGap(
                self.artifact, "missing_root_observation", "execution", self._seen_contexts, None
            )
        recipe = validate_physical_record(recipe)
        if recipe.kind != "recipe":
            raise ValueError("physical capture requires its supplied actual recipe")
        recipe = self._minimum_record(recipe)
        records: dict[str, PhysicalMetadata] = {recipe.metadata_id: recipe}
        header = self._root_header
        count = 0
        if header is not None:
            assert isinstance(header.metadata, PhysicalHeaders), (
                "root validation guarantees its header payload"
            )
            count = header.total("rows", len(header.metadata.rows))
            previous_id = header.metadata_id
            header = self._minimum_record(header)
            if header.metadata_id != previous_id:
                self._root_graph_hash = None
            records[header.metadata_id] = header
        graph_payload = PhysicalGraph(
            body_sha=self.artifact.sha256, header=header.metadata_id if header else None, edges=()
        )
        graph_omission = (
            self._omission(
                (("edges", count),),
                complete=self._root_context_complete,
                full_hash=self._root_graph_hash,
            )
            if count or not self._root_context_complete
            else None
        )
        graph = PhysicalMetadata.of(graph_payload, omission=graph_omission)
        records[graph.metadata_id] = graph
        for owner, _ in chain(order.requests, order.opens):
            if not 0 <= owner < self._seen_contexts:
                raise ValueError("actual event order refers to an unobserved context")
        for owner, _, _ in order.physical:
            if not 0 <= owner < self._seen_contexts:
                raise ValueError("actual physical order refers to an unobserved context")
        if any(not 0 <= owner < self._seen_contexts for owner, _ in order.zip_failures):
            raise ValueError("actual ZIP failure order refers to an unobserved context")
        counts = (
            ("contexts", self._seen_contexts),
            ("requests", len(order.requests)),
            ("opens", len(order.opens)),
            ("physical", len(order.physical)),
            ("zip_failures", len(order.zip_failures)),
        )
        root = PhysicalMetadata.of(
            PhysicalRootOrder(
                contexts=(),
                requests=(),
                opens=(),
                physical=(),
                zip_failures=(),
                legacy_rejections=order.legacy_rejections,
            ),
            body_sha256=self.artifact.sha256,
            omission=self._omission(counts, complete=complete, full_hash=self._root_order_hash),
        )
        records[root.metadata_id] = root
        if extraction is not None:
            extraction = ExtractionResult.model_validate_json(extraction.model_dump_json())
            if extraction.artifact_sha256 != self.artifact.sha256:
                raise ValueError("a physical capture cannot borrow another root's extraction")
        capture = PhysicalCapture.of(
            artifact_sha256=self.artifact.sha256,
            recipe_id=recipe.metadata_id,
            inventory_id=graph.metadata_id,
            execution_id=root.metadata_id,
            extraction_id=extraction.result_id if extraction else None,
        )
        if (
            sum(record.budget_bytes for record in records.values()) + capture.budget_bytes
            > MAX_PHYSICAL_ROOT_BYTES
        ):
            raise ValueError("minimum honest physical closure exceeds its complete packet budget")
        return PhysicalAssemblyPacket(
            (self.artifact,), tuple(records[key] for key in sorted(records)), capture
        )
