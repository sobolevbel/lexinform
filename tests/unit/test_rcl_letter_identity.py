import datetime as dt
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tools.source_history_doctor import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    ArtifactIdentity,
    RclLetterUse,
    RclLetterUseObservation,
    RclLetterUseRecord,
)
from lexinform.services.document_history import record_artifacts
from lexinform.services.rcl_letter_history import record_rcl_letter_uses
from tests.unit.test_rcl_letter_history import known_observation
from tests.unit.test_rcl_letter_use import NOW, letter_payload


def bound_observation(*, omitted: bool = True) -> RclLetterUseObservation:
    known = known_observation(omitted=omitted)
    payload = letter_payload(name="ą" * 300 if omitted else "Pismo.pdf").model_copy(
        update={"physical": known.use.payload.physical, "input": known.use.payload.input}
    )
    return RclLetterUseObservation(
        use=RclLetterUse.of_bound(payload),
        artifact=known.artifact,
        read=known.read,
        extraction=known.extraction,
    )


def replaced(use: RclLetterUse, path: tuple[str, ...], value: object) -> dict[str, Any]:
    values = use.model_dump(mode="json")
    current = values
    for key in path[:-1]:
        nested = current[key]
        assert isinstance(nested, dict), "Pydantic dumps the fixture's nested records as mappings"
        current = nested
    current[path[-1]] = value
    return values


@pytest.mark.parametrize("omitted", [False, True])
def test_bound_factory_keeps_full_identity_and_requires_actual_complete_input(
    omitted: bool,
) -> None:
    use = bound_observation(omitted=omitted).use
    old = known_observation(omitted=omitted).use
    assert old.use_id.startswith("rcl-letter-use-v1:")
    assert use.retained_identity_bound
    assert old.retained_identity_bound == (not omitted)
    assert use.payload == old.payload and use.semantic_sha256 == old.semantic_sha256
    assert (use.use_id != old.use_id) is omitted
    assert (
        RclLetterUse.model_validate_json(old.model_dump_json()).model_dump_json()
        == old.model_dump_json()
    )
    if not omitted:
        assert use.model_dump_json() == old.model_dump_json()
    first = RclLetterUseRecord(**use.model_dump(), recorded_at=NOW)
    later = RclLetterUseRecord(**use.model_dump(), recorded_at=NOW + dt.timedelta(days=365))
    assert first.use_id == later.use_id


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("payload", "official", "project_id"), 12402154),
        (("payload", "official", "stage_id"), None),
        (("payload", "official", "stage_number"), 99),
        (("payload", "official", "letter_id"), 737332),
        (("payload", "official", "letter_url_sha256"), "c" * 64),
        (("payload", "physical", "artifact_sha256"), "c" * 64),
        (("payload", "physical", "artifact_bytes"), 500),
        (("payload", "physical", "artifact_read_id"), None),
        (("payload", "input", "sha256"), "c" * 64),
        (("payload", "components", "parser_sha256"), "c" * 64),
        (("payload", "output_sha256"), "c" * 64),
        (("payload", "before_sha256"), "c" * 64),
        (("payload", "after_sha256"), "c" * 64),
        (("payload", "omitted"), ["record_bytes"]),
    ],
)
def test_bound_omission_rejects_changed_retained_facts_with_the_original_id(
    path: tuple[str, ...], value: object
) -> None:
    use = bound_observation().use
    changed = replaced(use, path, value)
    assert changed != use.model_dump(mode="json")
    with pytest.raises(ValueError, match="semantic and retained identity"):
        RclLetterUse.model_validate(changed)


def test_semantic_sha_is_bound_even_though_omitted_input_cannot_be_recovered() -> None:
    use = bound_observation().use
    changed = {**use.model_dump(mode="json"), "semantic_sha256": "c" * 64}
    with pytest.raises(ValueError, match="semantic and retained identity"):
        RclLetterUse.model_validate(changed)


def test_legacy_omission_is_preserved_and_reported_as_unbound(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    legacy = known_observation(omitted=True)
    with repo.atomic():
        record_rcl_letter_uses(repo, (legacy,), recorded_at=NOW)
    before = repo.dump()
    record = repo.get_rcl_letter_use(legacy.use.use_id)
    assert record is not None, "The caller persisted the fixture observation"
    assert not record.retained_identity_bound
    repo.close()
    result = audit(path)
    assert result["summary"]["rcl_letter_use_legacy_unbound_omissions"] == 1
    assert not result["findings"]
    restored = SqliteBillRepository(tmp_path / "restored.db")
    restored.restore(before)
    assert restored.dump() == before
    restored.close()


@pytest.mark.parametrize("known", [False, True])
def test_bound_omission_roundtrip_repeat_and_retention_preserve_identity(
    tmp_path: Path, known: bool
) -> None:
    observation = (
        bound_observation()
        if known
        else RclLetterUseObservation(use=RclLetterUse.of_bound(letter_payload(name="ą" * 300)))
    )
    repo = SqliteBillRepository(tmp_path / "history.db")
    repo.migrate()
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW)
    before = repo.dump()
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW + dt.timedelta(days=365))
        repo.prune_runs(before=NOW + dt.timedelta(days=366))
        repo.prune_history(
            before=NOW + dt.timedelta(days=366), memo_before=NOW + dt.timedelta(days=366)
        )
    assert repo.dump() == before
    record = repo.get_rcl_letter_use(observation.use.use_id)
    assert record is not None and record.retained_identity_bound, "The bound fact remains persisted"
    assert record.recorded_at == NOW
    restored = SqliteBillRepository(tmp_path / "restored.db")
    restored.restore(before)
    assert restored.dump() == before
    assert restored.get_rcl_letter_use(record.use_id) == record
    restored.close()
    repo.close()


def test_coordinated_valid_parent_tamper_is_rejected_before_restore_changes(tmp_path: Path) -> None:
    path = tmp_path / "history.db"
    repo = SqliteBillRepository(path)
    repo.migrate()
    observation = bound_observation()
    with repo.atomic():
        record_rcl_letter_uses(repo, (observation,), recorded_at=NOW)
        record_artifacts(repo, (ArtifactIdentity(sha256="c" * 64, byte_size=500),), recorded_at=NOW)
    before = repo.dump()
    record = repo.get_rcl_letter_use(observation.use.use_id)
    assert record is not None, "The caller persisted the bound observation"
    changed = record.model_dump(mode="json")
    changed["payload"]["physical"].update(
        artifact_sha256="c" * 64,
        artifact_bytes=500,
        artifact_read_id=None,
        extraction_id=None,
        input_extraction_relation="unknown",
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE rcl_letter_uses SET artifact_sha256=?,artifact_read_id=NULL,extraction_id=NULL,"
            "record_json=? WHERE use_id=?",
            (
                "c" * 64,
                json.dumps(changed, ensure_ascii=False, separators=(",", ":")),
                record.use_id,
            ),
        )
        connection.commit()
    with pytest.raises(ValueError, match="invalid_rcl_letter_use"):
        repo.get_rcl_letter_use(record.use_id)
    corrupted = repo.dump()
    repo.close()
    result = audit(path)
    assert result["findings"][0]["code"] == "invalid_rcl_letter_use"
    target = SqliteBillRepository(tmp_path / "target.db")
    target.restore(before)
    pristine = target.dump()
    with pytest.raises(ValueError, match="invalid_rcl_letter_use"):
        target.restore(corrupted)
    assert target.dump() == pristine
    target.close()
