import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tools.computation_doctor import audit

from tests.harness import CHANNEL, World
from tests.scenario.test_computation_doctor import computed_world as computed_world
from tests.scenario.test_request_doctor import snapshot

MARKER = "NEVER-EMIT-THE-OPAQUE-TARGET-REF"


def target(path: Path, kind: str, ref: str, *, term: int = 10) -> None:
    with closing(sqlite3.connect(path)) as database, database:
        database.execute(
            "UPDATE result_applications SET term=?, generation=NULL, application_json="
            "json_set(json_remove(application_json, '$.projected_generation'),"
            " '$.term', ?, '$.generation', NULL, '$.target', json(?)) "
            "WHERE phase='projection'",
            (term, term, json.dumps({"kind": kind, "ref": ref})),
        )


@pytest.mark.parametrize(
    "kind",
    [
        "status_change",
        "publication",
        "command",
        "bill",
        "editorial",
        "consultation_review",
        "commission_review",
        "act_application",
    ],
)
def test_known_target_linkage_does_not_compare_current_slot_contents(
    computed_world: World,
    tmp_path: Path,
    kind: str,
) -> None:
    path = snapshot(computed_world, tmp_path)
    with closing(sqlite3.connect(path)) as database, database:
        if kind == "status_change":
            ref = str(database.execute("SELECT id FROM status_changes").fetchone()[0])
        elif kind == "publication":
            ref = str(
                database.execute(
                    "SELECT id FROM publications WHERE ref='graph-fixture'"
                ).fetchone()[0]
            )
        elif kind == "command":
            database.execute(
                "INSERT INTO commands(update_id, chat_id, message_id, text, received_at) "
                "VALUES(0, ?, 1, ?, ?)",
                (CHANNEL, MARKER, computed_world.clock.now().isoformat()),
            )
            ref = "0"
        elif kind == "bill":
            ref = "joint"
        elif kind == "editorial":
            ref = "mnemonic"
        else:
            ref = f"{kind}/recorded-source-key"
            kind = "document_review"
    target(path, kind, ref)
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["findings"] == []
    links = report["target_links"]
    assert len(links) == 1 and links[0]["kind"] == kind
    assert links[0]["status"] == (
        "present" if kind in ("status_change", "publication", "command") else "owner_present"
    )
    assert MARKER not in json.dumps(report) and path.read_bytes() == before


@pytest.mark.parametrize("kind", ["status_change", "publication", "command"])
def test_unavailable_physical_target_is_a_warning_without_repair(
    computed_world: World,
    tmp_path: Path,
    kind: str,
) -> None:
    path = snapshot(computed_world, tmp_path)
    target(path, kind, "999999999")
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["summary"]["errors"] == 0
    assert report["summary"]["warnings"] == 1
    assert report["target_links"][0]["status"] == "row_unavailable"
    assert report["findings"][0]["code"] == "application_target_unavailable"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("kind", "ref"),
    [
        ("future_owner", MARKER),
        ("publication", "001"),
        ("command", "opaque/1"),
        ("status_change", str(2**64)),
        ("bill", "future_field"),
    ],
)
def test_unsupported_reference_codecs_remain_opaque(
    computed_world: World,
    tmp_path: Path,
    kind: str,
    ref: str,
) -> None:
    path = snapshot(computed_world, tmp_path)
    target(path, kind, ref)
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["findings"] == []
    assert report["target_links"][0]["status"] == "opaque"
    assert MARKER not in json.dumps(report) and path.read_bytes() == before


def test_missing_historical_logical_owner_does_not_invalidate_the_receipt(
    computed_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(computed_world, tmp_path)
    target(path, "bill", "joint", term=9)
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["findings"] == []
    assert report["target_links"][0]["status"] == "historical_owner_absent"
    assert path.read_bytes() == before


def test_missing_target_table_is_explicit_without_discarding_the_graph(
    computed_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(computed_world, tmp_path)
    target(path, "command", "0")
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("DROP TABLE commands")
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["summary"]["warnings"] == 1
    assert report["target_links"][0]["status"] == "table_unavailable"
    assert path.read_bytes() == before


def test_review_keys_are_observed_without_echoing_or_recomputing_them(
    computed_world: World,
    tmp_path: Path,
) -> None:
    path = snapshot(computed_world, tmp_path)
    target(path, "document_review", f"consultation_review/{MARKER}")
    before = path.read_bytes()

    report = audit(path)

    assert report["status"] == "recorded" and report["findings"] == []
    assert report["target_links"][0]["status"] == "owner_present"
    assert MARKER not in json.dumps(report) and path.read_bytes() == before
