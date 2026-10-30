import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.physical_history import PhysicalInventoryRowError
from lexinform.adapters.sqlite_repo import MIGRATIONS, SCHEMA_VERSION, SqliteBillRepository
from lexinform.models import ArtifactMetadata
from lexinform.models.physical_inventory import (
    HeaderRow,
    PhysicalCapture,
    PhysicalCaptureOptions,
    PhysicalCaptureRecipe,
    PhysicalContainerExecution,
    PhysicalGraph,
    PhysicalHeaders,
    PhysicalMetadata,
    PhysicalOmission,
    PhysicalOmittedCount,
    PhysicalRootOrder,
    physical_sha256,
)
from tests.unit.test_physical_extraction import docx, extractor

NOW = dt.datetime(2026, 10, 29, tzinfo=dt.UTC)
ROOT_SHA = "a" * 64


def recipe() -> PhysicalMetadata:
    payload = PhysicalCaptureRecipe.model_validate(
        {
            "mode": "observer",
            "code_files": [{"path": "fixture.py", "sha256": "c" * 64}],
            "implementations": [
                {"role": role, "qualified_name": "fixture." + role, "code_path": "fixture.py"}
                for role in ("package", "pdf", "word", "doc", "odt", "capture")
            ],
            "dependencies": [
                {"name": name, "version": "fixture-v1"}
                for name in ("python", "pypdf", "olefile", "inflate64", "pydantic", "pydantic_core")
            ],
            "options": PhysicalCaptureOptions(
                max_root_bytes=32000000,
                max_member_bytes=32000000,
                max_part_chars=400000,
                archive_depth=4,
                archive_bytes=128000000,
                archive_members=2048,
                xml_bytes=32000000,
                include_pdf_pages=True,
                include_word_blocks=True,
                include_word_notes=True,
                include_odt_blocks=True,
                include_unlocated_text=True,
            ),
        }
    )
    return PhysicalMetadata.of(payload)


def header_row(name: str, size: int = 100000) -> HeaderRow:
    return (name, None, size, 1, 0, 0, 0, 0, 0, 0, (0, None), (0, None), ())


@dataclass(frozen=True)
class Case:
    header: PhysicalMetadata
    graph: PhysicalMetadata
    unit: PhysicalMetadata
    order: PhysicalMetadata
    recipe: PhysicalMetadata
    capture: PhysicalCapture

    @property
    def records(self) -> tuple[PhysicalMetadata, ...]:
        return self.header, self.graph, self.unit, self.order, self.recipe


def case(body: str = ROOT_SHA) -> Case:
    header = PhysicalMetadata.of(
        PhysicalHeaders(
            body_sha=body,
            central_directory_sha256="b" * 64,
            central_directory_span=(10, 20),
            archive_comment=(0, None),
            rows=(header_row("project.docx"), header_row("second.docx")),
        )
    )
    graph = PhysicalMetadata.of(
        PhysicalGraph(body_sha=body, header=header.metadata_id, edges=((None, None), (None, None)))
    )
    unit = PhysicalMetadata.of(
        PhysicalContainerExecution(
            graph=graph.metadata_id,
            route="archive",
            propagated_error=None,
            format_error=None,
            statuses=("readability_rejected", "readability_rejected"),
            requests=(),
            opens=(),
        ),
        body_sha256=body,
        header_id=header.metadata_id,
    )
    order = PhysicalMetadata.of(
        PhysicalRootOrder(
            contexts=((None, None, None, unit.metadata_id),),
            requests=(),
            opens=(),
            physical=(),
            zip_failures=(),
            legacy_rejections=(0, "d" * 64),
        ),
        body_sha256=body,
    )
    code = recipe()
    capture = PhysicalCapture.of(
        artifact_sha256=body,
        recipe_id=code.metadata_id,
        inventory_id=graph.metadata_id,
        execution_id=order.metadata_id,
    )
    return Case(header, graph, unit, order, code, capture)


@pytest.fixture
def repo() -> Iterator[SqliteBillRepository]:
    value = SqliteBillRepository(":memory:")
    value.migrate()
    yield value
    value.close()


def save(repo: SqliteBillRepository, value: Case) -> None:
    repo.save_artifact_metadata(
        ArtifactMetadata(sha256=value.capture.artifact_sha256, byte_size=1000, recorded_at=NOW)
    )
    repo.save_physical_inventory(tuple(reversed(value.records)), (value.capture,), recorded_at=NOW)


def test_physical_reader_keeps_zero_part_root_and_recipe_without_extraction(
    repo: SqliteBillRepository,
) -> None:
    value = case()
    save(repo, value)
    capture = repo.get_physical_capture(value.capture.capture_id)
    assert capture is not None, "saving a capture guarantees its exact immutable row"
    assert capture.extraction_id is None
    assert capture.recorded_at == NOW
    assert capture.recipe_id == value.recipe.metadata_id
    stored_recipe = repo.get_physical_metadata(capture.recipe_id)
    assert stored_recipe is not None, "capture FK retains its reproducible recipe"
    assert stored_recipe.metadata == value.recipe.metadata
    assert list(repo.iter_physical_captures(ROOT_SHA)) == [capture]
    before = repo.dump()
    repo.save_physical_inventory(
        value.records, (value.capture,), recorded_at=NOW + dt.timedelta(days=1)
    )
    assert repo.dump() == before


def test_reader_validates_structure_without_reapplying_current_filename_policy(
    repo: SqliteBillRepository,
) -> None:
    value = case()
    save(repo, value)
    assert repo.get_physical_metadata(value.unit.metadata_id) is not None


def test_wrong_root_rolls_back_new_metadata_and_capture(repo: SqliteBillRepository) -> None:
    first, second = case(), case("b" * 64)
    save(repo, first)
    repo.save_artifact_metadata(
        ArtifactMetadata(sha256=second.capture.artifact_sha256, byte_size=1000, recorded_at=NOW)
    )
    wrong = PhysicalCapture.of(
        artifact_sha256=second.capture.artifact_sha256,
        recipe_id=first.recipe.metadata_id,
        inventory_id=first.graph.metadata_id,
        execution_id=first.order.metadata_id,
    )
    before = repo.dump()
    with pytest.raises(PhysicalInventoryRowError, match="owner_mismatch"):
        repo.save_physical_inventory(second.records, (wrong,), recorded_at=NOW)
    assert repo.dump() == before


def test_missing_or_wrong_kind_references_are_rejected_atomically(
    repo: SqliteBillRepository,
) -> None:
    value = case()
    repo.save_physical_metadata(value.recipe, recorded_at=NOW)
    before = repo.dump()
    for target in ("e" * 64, value.recipe.metadata_id):
        wrong = PhysicalMetadata.of(PhysicalGraph(body_sha=ROOT_SHA, header=target, edges=()))
        with pytest.raises(PhysicalInventoryRowError):
            repo.save_physical_metadata(wrong, recorded_at=NOW)
        assert repo.dump() == before


def test_known_extraction_association_requires_its_real_outer_body(
    repo: SqliteBillRepository,
) -> None:
    data = docx("USTAWA o pobycie cudzoziemców")
    result = extractor().extract_document_with_provenance(data).result
    body = hashlib.sha256(data).hexdigest()
    value = case(body)
    repo.save_artifact_metadata(ArtifactMetadata(sha256=body, byte_size=len(data), recorded_at=NOW))
    repo.save_extraction_result(result, recorded_at=NOW)
    known = PhysicalCapture.of(
        artifact_sha256=body,
        recipe_id=value.recipe.metadata_id,
        inventory_id=value.graph.metadata_id,
        execution_id=value.order.metadata_id,
        extraction_id=result.result_id,
    )
    repo.save_physical_inventory(value.records, (known,), recorded_at=NOW)
    assert repo.get_physical_capture(known.capture_id) is not None
    other = case()
    save(repo, other)
    wrong = PhysicalCapture.of(
        artifact_sha256=ROOT_SHA,
        recipe_id=other.recipe.metadata_id,
        inventory_id=other.graph.metadata_id,
        execution_id=other.order.metadata_id,
        extraction_id=result.result_id,
    )
    before = repo.dump()
    with pytest.raises(PhysicalInventoryRowError, match="extraction_mismatch"):
        repo.save_physical_capture(wrong, recorded_at=NOW)
    assert repo.dump() == before


def test_partial_identity_binds_owner_retained_fields_and_full_digest() -> None:
    payload = PhysicalRootOrder(
        contexts=(),
        requests=(),
        opens=(),
        physical=(),
        zip_failures=(),
        legacy_rejections=(0, "d" * 64),
    )
    missing = PhysicalOmission(
        reason="missing_metadata", full_metadata_sha256=None, unknown_fields=1
    )
    first = PhysicalMetadata.of(payload, body_sha256=ROOT_SHA, omission=missing)
    second = PhysicalMetadata.of(payload, body_sha256="b" * 64, omission=missing)
    opaque = PhysicalMetadata.of(
        payload,
        body_sha256=ROOT_SHA,
        omission=PhysicalOmission(
            reason="over_limit",
            full_metadata_sha256="c" * 64,
            omitted=(PhysicalOmittedCount(field="contexts", count=2),),
        ),
    )
    assert len({first.metadata_id, second.metadata_id, opaque.metadata_id}) == 3
    assert first.scope == "observed" and first.coverage == "partial"
    with pytest.raises(ValueError, match="opaque"):
        PhysicalOmission(reason="over_limit", full_metadata_sha256=None, unknown_fields=1)


def test_complete_recipe_cannot_omit_code_or_versions() -> None:
    record = recipe()
    assert isinstance(record.metadata, PhysicalCaptureRecipe), (
        "recipe fixture guarantees its payload"
    )
    bad = record.metadata.model_copy(update={"dependencies": ()})
    with pytest.raises(ValueError, match="reproducible"):
        PhysicalMetadata.of(bad)


def test_persisted_envelope_budget_includes_timestamp_and_wrapper() -> None:
    header = PhysicalHeaders(
        body_sha=ROOT_SHA,
        central_directory_sha256="b" * 64,
        central_directory_span=(0, 1),
        archive_comment=(0, None),
        rows=(header_row("x" * 33000),),
    )
    with pytest.raises(ValueError, match="envelope byte budget"):
        PhysicalMetadata.of(header)


def exact_cap_header(name_length: int) -> PhysicalMetadata:
    header = PhysicalHeaders(
        body_sha=ROOT_SHA,
        central_directory_sha256="b" * 64,
        central_directory_span=(0, 1),
        archive_comment=(0, None),
        rows=(header_row("x" * name_length),),
    )
    return PhysicalMetadata(
        metadata_id=physical_sha256(header.model_dump(mode="json")),
        kind="headers",
        body_sha256=ROOT_SHA,
        header_id=None,
        metadata=header,
        recorded_at=NOW,
    )


def test_exact_cap_repeat_validates_with_the_persisted_first_recording_time(
    repo: SqliteBillRepository,
) -> None:
    small = exact_cap_header(1)
    record = exact_cap_header(1 + 32768 - small.budget_bytes)
    assert record.budget_bytes == 32768
    repo.save_physical_metadata(record, recorded_at=NOW)
    before = repo.dump()

    repo.save_physical_metadata(record, recorded_at=NOW + dt.timedelta(microseconds=1))

    assert repo.dump() == before
    saved = repo.get_physical_metadata(record.metadata_id)
    assert saved is not None, "the first save retained its exact-cap immutable header"
    assert saved.recorded_at == NOW and saved.budget_bytes == 32768


def test_structural_schedule_and_local_request_failures_roll_back(
    repo: SqliteBillRepository,
) -> None:
    value = case()
    save(repo, value)
    assert isinstance(value.order.metadata, PhysicalRootOrder), "case builder owns root order"
    invalid = PhysicalMetadata.of(
        value.order.metadata.model_copy(update={"requests": ((0, 999),)}),
        body_sha256=ROOT_SHA,
    )
    before = repo.dump()
    with pytest.raises(PhysicalInventoryRowError, match="parent_mismatch"):
        repo.save_physical_metadata(invalid, recorded_at=NOW)
    assert repo.dump() == before


def test_sql_null_uniqueness_and_reference_views(tmp_path: Path) -> None:
    path = tmp_path / "physical.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    value = case()
    save(repository, value)
    repository.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("SELECT COUNT(*) FROM physical_capture_refs").fetchone() == (3,)
        assert connection.execute("SELECT COUNT(*) FROM physical_recipe_code").fetchone() == (1,)
        query = connection.execute(
            "EXPLAIN QUERY PLAN SELECT rowid FROM physical_metadata WHERE header_id=?",
            (value.header.metadata_id,),
        ).fetchone()
        assert query is not None, "SQLite returns the lookup plan for the physical header FK"
        assert "USING COVERING INDEX ix_physical_metadata_header" in query[3]
        for column, suffix in (
            ("artifact_sha256", "artifact"),
            ("recipe_id", "recipe"),
            ("inventory_id", "inventory"),
            ("execution_id", "execution"),
            ("extraction_id", "extraction"),
        ):
            query = connection.execute(
                f"EXPLAIN QUERY PLAN SELECT rowid FROM physical_captures WHERE {column}=?",
                ("a" * 64,),
            ).fetchone()
            assert query is not None, "SQLite returns a plan for every physical capture FK"
            assert f"USING COVERING INDEX ix_physical_capture_{suffix}" in query[3]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO physical_captures SELECT ?,artifact_sha256,recipe_id,inventory_id,"
                "execution_id,extraction_id,recorded_at,record_json FROM physical_captures",
                ("f" * 64,),
            )
    assert audit(path)["status"] == "recorded"


def test_get_doctor_and_scratch_restore_reject_retained_field_tampering(tmp_path: Path) -> None:
    path = tmp_path / "physical.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    value = case()
    save(repository, value)
    good = repository.dump()
    repository.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        raw = connection.execute(
            "SELECT record_json FROM physical_metadata WHERE metadata_id=?",
            (value.header.metadata_id,),
        ).fetchone()[0]
        data = json.loads(raw)
        data["metadata"]["rows"][0][0] = "changed.docx"
        connection.execute(
            "UPDATE physical_metadata SET record_json=? WHERE metadata_id=?",
            (json.dumps(data), value.header.metadata_id),
        )
    damaged = SqliteBillRepository(path)
    with pytest.raises(PhysicalInventoryRowError, match="invalid_physical_metadata"):
        damaged.get_physical_capture(value.capture.capture_id)
    broken = damaged.dump()
    damaged.close()
    assert audit(path)["status"] == "failed"
    target = SqliteBillRepository(":memory:")
    target.migrate()
    target.restore(good)
    before = target.dump()
    with pytest.raises(PhysicalInventoryRowError):
        target.restore(broken)
    assert target.dump() == before
    target.close()


@pytest.mark.parametrize("field", ["byte_size", "recorded_at"])
def test_corrupt_artifact_parent_is_rejected_without_replacing_restored_state(
    tmp_path: Path, field: str
) -> None:
    path = tmp_path / "corrupt-parent.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    value = case()
    save(repository, value)
    good = repository.dump()
    repository.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"UPDATE artifacts SET {field}=?", ("corrupt",))
    damaged = SqliteBillRepository(path)
    with pytest.raises(PhysicalInventoryRowError, match="physical_capture_artifact_invalid"):
        damaged.get_physical_capture(value.capture.capture_id)
    broken = damaged.dump()
    damaged.close()
    assert audit(path)["status"] == "failed"
    target = SqliteBillRepository(":memory:")
    target.restore(good)
    before = target.dump()
    with pytest.raises(PhysicalInventoryRowError, match="physical_capture_artifact_invalid"):
        target.restore(broken)
    assert target.dump() == before
    target.close()


def test_repeat_restore_drops_old_views_and_preserves_exact_envelopes(
    repo: SqliteBillRepository,
) -> None:
    value = case()
    save(repo, value)
    script = repo.dump()
    repo.restore(script)
    repo.restore(script)
    assert repo.dump() == script
    assert repo.get_physical_capture(value.capture.capture_id) is not None


def test_schema61_prefix_and_old_dump_migrate_without_rewriting_existing_rows() -> None:
    prefix = json.dumps(MIGRATIONS[:61], ensure_ascii=False, separators=(",", ":")).encode()
    assert hashlib.sha256(prefix).hexdigest() == (
        "c9c26fdc869f8ed4d56d4281b620f15ed8ccd6671ad9fdf018d27704a609ffba"
    )
    legacy = sqlite3.connect(":memory:")
    for number, migration in enumerate(MIGRATIONS[:61], 1):
        legacy.executescript(f"BEGIN;{migration}PRAGMA user_version={number};COMMIT;")
    script = "\n".join(legacy.iterdump()) + "\nPRAGMA user_version = 61;\n"
    legacy.close()
    current = SqliteBillRepository(":memory:")
    current.restore(script)
    assert current.schema_version == SCHEMA_VERSION
    assert list(current.iter_physical_captures()) == []
    current.close()


def test_capture_hash_binds_nullable_actual_association() -> None:
    value = case().capture
    known = PhysicalCapture.of(
        artifact_sha256=value.artifact_sha256,
        recipe_id=value.recipe_id,
        inventory_id=value.inventory_id,
        execution_id=value.execution_id,
        extraction_id="extraction-v1:" + "b" * 64,
    )
    assert value.capture_id != known.capture_id
    raw = known.model_dump(mode="json")
    raw["extraction_id"] = None
    with pytest.raises(ValueError, match="identity"):
        PhysicalCapture.model_validate(raw)
    assert physical_sha256({"recipe": value.recipe_id}) != value.capture_id


def test_partial_unknown_recipe_preserves_known_fields_without_legacy_inference(
    repo: SqliteBillRepository,
) -> None:
    value = recipe()
    assert isinstance(value.metadata, PhysicalCaptureRecipe), (
        "recipe builder guarantees its payload"
    )
    unknown = value.metadata.model_copy(
        update={"code_files": (value.metadata.code_files[0].model_copy(update={"sha256": None}),)}
    )
    omitted = PhysicalMetadata.of(
        unknown,
        omission=PhysicalOmission(
            reason="missing_metadata", full_metadata_sha256=None, unknown_fields=1
        ),
    )
    repo.save_physical_metadata(omitted, recorded_at=NOW)
    stored = repo.get_physical_metadata(omitted.metadata_id)
    assert stored is not None, "saved partial recipe remains independently readable"
    assert stored.coverage == "partial" and stored.metadata == unknown


def test_distinct_transitive_union_overflow_rolls_back_whole_capture(
    repo: SqliteBillRepository,
) -> None:
    value = case()
    children: list[PhysicalMetadata] = []
    edges: list[tuple[str, str]] = []
    units: list[PhysicalMetadata] = []
    for index in range(26):
        body = physical_sha256(["large child", index])
        header = PhysicalMetadata.of(
            PhysicalHeaders(
                body_sha=body,
                central_directory_sha256="b" * 64,
                central_directory_span=(0, 20),
                archive_comment=(0, None),
                rows=(header_row("x" * 24000 + str(index)),),
            )
        )
        graph = PhysicalMetadata.of(
            PhysicalGraph(body_sha=body, header=header.metadata_id, edges=((None, None),))
        )
        unit = PhysicalMetadata.of(
            PhysicalContainerExecution(
                graph=graph.metadata_id,
                route="archive",
                propagated_error=None,
                format_error=None,
                statuses=("uninspected_format_part",),
                requests=(),
                opens=(),
            ),
            body_sha256=body,
            header_id=header.metadata_id,
        )
        children.extend((header, graph, unit))
        edges.append((body, graph.metadata_id))
        units.append(unit)
    header = PhysicalMetadata.of(
        PhysicalHeaders(
            body_sha=ROOT_SHA,
            central_directory_sha256="b" * 64,
            central_directory_span=(0, 20),
            archive_comment=(0, None),
            rows=tuple(header_row("child.zip") for _ in edges),
        )
    )
    graph = PhysicalMetadata.of(
        PhysicalGraph(body_sha=ROOT_SHA, header=header.metadata_id, edges=tuple(edges))
    )
    unit = PhysicalMetadata.of(
        PhysicalContainerExecution(
            graph=graph.metadata_id,
            route="archive",
            propagated_error=None,
            format_error=None,
            statuses=tuple("uninspected_format_part" for _ in edges),
            requests=(),
            opens=(),
        ),
        body_sha256=ROOT_SHA,
        header_id=header.metadata_id,
    )
    order = PhysicalMetadata.of(
        PhysicalRootOrder(
            contexts=(
                (None, None, None, unit.metadata_id),
                *((0, index, None, child.metadata_id) for index, child in enumerate(units)),
            ),
            requests=(),
            opens=(),
            physical=(),
            zip_failures=(),
            legacy_rejections=(0, "d" * 64),
        ),
        body_sha256=ROOT_SHA,
    )
    capture = PhysicalCapture.of(
        artifact_sha256=ROOT_SHA,
        recipe_id=value.recipe.metadata_id,
        inventory_id=graph.metadata_id,
        execution_id=order.metadata_id,
    )
    repo.save_artifact_metadata(ArtifactMetadata(sha256=ROOT_SHA, byte_size=1000, recorded_at=NOW))
    before = repo.dump()
    with pytest.raises(PhysicalInventoryRowError, match="root_over_budget"):
        repo.save_physical_inventory(
            (*children, header, graph, unit, order, value.recipe), (capture,), recorded_at=NOW
        )
    assert repo.dump() == before


def test_cycle_tampering_is_rejected_by_save_get_doctor_and_restore(tmp_path: Path) -> None:
    path = tmp_path / "cycle.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    value = case()
    save(repository, value)
    assert isinstance(value.graph.metadata, PhysicalGraph), "case builder guarantees its graph"
    forged = value.graph.model_copy(
        update={
            "header_id": value.graph.metadata_id,
            "metadata": value.graph.metadata.model_copy(update={"header": value.graph.metadata_id}),
        }
    )
    before = repository.dump()
    with pytest.raises(ValueError):
        repository.save_physical_metadata(forged, recorded_at=NOW)
    assert repository.dump() == before
    repository.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE physical_metadata SET header_id=?,record_json=? WHERE metadata_id=?",
            (forged.header_id, forged.model_dump_json(), forged.metadata_id),
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM physical_metadata_refs WHERE owner_id=target_id"
            ).fetchone()[0]
            == 1
        )
    damaged = SqliteBillRepository(path)
    with pytest.raises(PhysicalInventoryRowError):
        damaged.get_physical_capture(value.capture.capture_id)
    broken = damaged.dump()
    damaged.close()
    assert audit(path)["status"] == "failed"
    target = SqliteBillRepository(":memory:")
    target.migrate()
    with pytest.raises(PhysicalInventoryRowError):
        target.restore(broken)
    target.close()


def test_missing_or_redefined_sql_reference_view_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "view.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    value = case()
    save(repository, value)
    repository.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP VIEW physical_metadata_refs")
        connection.execute(
            "CREATE VIEW physical_metadata_refs AS SELECT metadata_id AS owner_id,"
            "metadata_id AS target_id,'hidden' AS relation FROM physical_metadata"
        )
    damaged = SqliteBillRepository(path)
    with pytest.raises(PhysicalInventoryRowError, match="schema_mismatch"):
        damaged.get_physical_capture(value.capture.capture_id)
    damaged.close()
    assert audit(path)["status"] == "failed"


def test_named_read_resolution_is_not_replaced_by_enumerated_or_lookup_ordinal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate.db"
    repository = SqliteBillRepository(path)
    repository.migrate()
    child_body = "b" * 64
    header = PhysicalMetadata.of(
        PhysicalHeaders(
            body_sha=ROOT_SHA,
            central_directory_sha256="c" * 64,
            central_directory_span=(0, 20),
            archive_comment=(0, None),
            rows=(header_row("same.docx", 100), header_row("same.docx", 100)),
        )
    )
    child = PhysicalMetadata.of(PhysicalGraph(body_sha=child_body, header=None, edges=()))
    graph = PhysicalMetadata.of(
        PhysicalGraph(
            body_sha=ROOT_SHA,
            header=header.metadata_id,
            edges=((None, None), (child_body, child.metadata_id)),
        )
    )
    unit = PhysicalMetadata.of(
        PhysicalContainerExecution(
            graph=graph.metadata_id,
            route="archive",
            propagated_error=None,
            format_error=None,
            statuses=("member_complete", "uninspected_format_part"),
            requests=(("member", "ordinal", 0, 0, "complete", 100, child_body, None),),
            opens=((0, "name", "same.docx", 1, "opened", None),),
        ),
        body_sha256=ROOT_SHA,
        header_id=header.metadata_id,
    )
    child_unit = PhysicalMetadata.of(
        PhysicalContainerExecution(
            graph=child.metadata_id,
            route="non_zip",
            propagated_error=None,
            format_error=None,
            statuses=(),
            requests=(),
            opens=(),
        ),
        body_sha256=child_body,
    )
    order = PhysicalMetadata.of(
        PhysicalRootOrder(
            contexts=((None, None, None, unit.metadata_id), (0, 1, 0, child_unit.metadata_id)),
            requests=((0, 0),),
            opens=((0, 0),),
            physical=((1, "c" * 64, "d" * 64),),
            zip_failures=(),
            legacy_rejections=(0, "d" * 64),
        ),
        body_sha256=ROOT_SHA,
    )
    code = recipe()
    capture = PhysicalCapture.of(
        artifact_sha256=ROOT_SHA,
        recipe_id=code.metadata_id,
        inventory_id=graph.metadata_id,
        execution_id=order.metadata_id,
    )
    repository.save_artifact_metadata(
        ArtifactMetadata(sha256=ROOT_SHA, byte_size=1000, recorded_at=NOW)
    )
    repository.save_physical_inventory(
        (header, child, graph, unit, child_unit, order, code), (capture,), recorded_at=NOW
    )
    assert repository.get_physical_capture(capture.capture_id) is not None
    repository.close()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT lookup_ordinal FROM physical_requests").fetchone() == (0,)
        assert connection.execute("SELECT actual_read_ordinal FROM physical_opens").fetchone() == (
            1,
        )
