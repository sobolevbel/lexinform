"""The legacy cleanup changes only never-analyzed regulations with a matching saved rejection."""

import json
import sqlite3
from contextlib import closing

import pytest

from lexinform.adapters.sqlite_repo import MIGRATIONS
from lexinform.models import process_summary
from tests.harness import rcl_project

TITLE = "projekt rozporządzenia Ministra Spraw Zagranicznych w sprawie opłat konsularnych"
REASON = "text prefilter: unsupported: https://legislacja.rcl.gov.pl/docs/dokument802701.docx: regulation"
MIGRATION_VERSION = 76


@pytest.mark.parametrize(
    "protection",
    [
        None,
        "statute",
        "non_rcl",
        "missing_rcl",
        "different_error",
        "ambiguous",
        "analysis",
        "attempt",
        "publication",
        "batch_item",
        "batch_intent",
        "document_work",
    ],
)
def test_migration_preserves_sources_and_protected_owners(protection: str | None) -> None:
    with closing(sqlite3.connect(":memory:")) as db:
        for migration in MIGRATIONS[: MIGRATION_VERSION - 1]:
            db.executescript(migration)
        project = rcl_project(title=TITLE)
        summary = process_summary(rcl_project(), term=10).model_copy(update={"title": TITLE})
        number = "4000" if protection == "non_rcl" else project.number
        source = project.model_dump_json()
        if protection == "statute":
            source = rcl_project(
                title="Projekt ustawy o wykonywaniu rozporządzenia UE"
            ).model_dump_json()
        db.execute(
            "INSERT INTO bills(term,number,title,change_date,status,summary_json,rcl_json,"
            "last_error,first_seen_at,last_checked_at,analysis_json,analysis_attempts)"
            " VALUES(10,?,?, '2026-11-13', ?,?,?,?, '2026-11-13','2026-11-13',?,?)",
            (
                number,
                TITLE,
                "document_ambiguous" if protection == "ambiguous" else "document_unsupported",
                summary.model_dump_json(),
                None if protection == "missing_rcl" else source,
                "unreadable file" if protection == "different_error" else REASON,
                "{}" if protection == "analysis" else None,
                int(protection == "attempt"),
            ),
        )
        if protection == "publication":
            db.execute(
                "INSERT INTO publications(term,number,kind,status,channel_id,created_at)"
                " VALUES(10,?,'new_bill','unknown','@test','2026-11-13')",
                (number,),
            )
        elif protection == "batch_item":
            db.execute(
                "INSERT INTO llm_batch_items(batch_id,custom_id,call_kind,term,number,meta_json)"
                " VALUES('test','test','analysis',10,?,'{}')",
                (number,),
            )
        elif protection == "batch_intent":
            db.execute(
                "INSERT INTO llm_batch_intents(custom_id,provider,request_json,meta_json,state,created_at)"
                " VALUES('test','openai',?,'{}','queued','2026-11-13')",
                (json.dumps({"term": 10, "number": number}),),
            )
        elif protection == "document_work":
            db.execute(
                "INSERT INTO document_work(key,term,number,state,next_at,work_json)"
                " VALUES('test',10,?,'waiting','2026-11-13','{}')",
                (number,),
            )
        db.row_factory = sqlite3.Row
        before = dict(db.execute("SELECT * FROM bills").fetchone())

        db.executescript(MIGRATIONS[MIGRATION_VERSION - 1])

        after = dict(db.execute("SELECT * FROM bills").fetchone())
        if protection is None:
            assert after.pop("status") == "skipped_prefilter"
            assert str(after.pop("last_error")).endswith(f"previous: {REASON}")
            before.pop("status")
            before.pop("last_error")
        assert after == before
        first_dump = "\n".join(db.iterdump())
        db.executescript(MIGRATIONS[MIGRATION_VERSION - 1])
        assert "\n".join(db.iterdump()) == first_dump
