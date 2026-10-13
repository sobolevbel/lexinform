import datetime as dt
import json
from pathlib import Path

from tools.message_usefulness_audit import audit

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus


def test_audit_preserves_frozen_text_and_excludes_missing_history(tmp_path: Path) -> None:
    repo = SqliteBillRepository(":memory:")
    repo.migrate()
    for number, plan in (
        ("DIGEST", DeliveryPlan(bill_json="", rendered_text="saved text")),
        ("3039", None),
    ):
        repo.create_publication(
            Publication(
                term=0,
                number=number,
                kind=PublicationKind.DIGEST,
                status=PublicationStatus.SENT,
                channel_id="@test",
                ref=number,
                created_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
                delivery=plan,
            )
        )
    dump = tmp_path / "state.sql"
    dump.write_text(repo.dump())

    manifest = audit(dump, tmp_path / "rendered")

    assert manifest["restored_payloads"] == 1
    exclusions = manifest["exclusions"]
    assert isinstance(exclusions, list) and len(exclusions) == 1
    assert (tmp_path / "rendered/1-ru.html").read_text() == "saved text\n"
    assert (
        json.loads((tmp_path / "rendered/manifest.json").read_text())["rubric"][
            "unsafe_outcome_or_deadline_cap"
        ]
        == 4
    )
