from collections.abc import Callable
from pathlib import Path

import pytest
from tools import (
    audit_saved_primary_claims,
    batch_integrity_doctor,
    computation_doctor,
    content_history_doctor,
    document_read_profile,
    legacy_history,
    request_doctor,
    retained_integrity_doctor,
    source_history_doctor,
    work_doctor,
)


@pytest.mark.parametrize(
    "run",
    [
        audit_saved_primary_claims.run,
        batch_integrity_doctor.run,
        computation_doctor.run,
        content_history_doctor.run,
        document_read_profile.run,
        legacy_history.run,
        request_doctor.run,
        retained_integrity_doctor.run,
        source_history_doctor.run,
        work_doctor.run,
    ],
    ids=lambda run: run.__module__.removeprefix("tools."),
)
def test_snapshot_report_rejects_canonical_sidecar_before_reading(
    tmp_path: Path, run: Callable[[Path, Path], int]
) -> None:
    database = tmp_path / "checkpoint.db"
    alias = tmp_path / "alias.db"
    alias.symlink_to(database)
    sidecar = tmp_path / "checkpoint.db-wal"

    with pytest.raises(ValueError, match="Report must be separate"):
        run(alias, sidecar)

    assert not sidecar.exists()
