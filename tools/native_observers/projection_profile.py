import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from tests.harness import World
from tests.scenario.test_amendments_command import published_world
from tests.scenario.test_explain import commands_only
from tests.scenario.test_joint_batch import joint_world
from tests.scenario.test_review_computation_history import queued_review, submit_review

import lexinform
from lexinform.models import ReviewWork
from lexinform.services.tracking_result import TrackingResult


def snapshot(world, output, name, number="3039"):
    raw = world.repo.dump().encode()
    (output / f"{name}.sql").write_bytes(raw)
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(raw.decode())
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        data = {}
        for (table,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall():
            query = f"SELECT * FROM {table}"
            if table == "result_applications":
                query += " WHERE generation IS NOT NULL"
            rows = sorted(json.dumps(row, ensure_ascii=False) for row in db.execute(query))
            data[table] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
        sqlite_bytes = len(db.serialize())
    return {
        "data": data,
        "sql_bytes": len(raw),
        "gzip_bytes": len(gzip.compress(raw, mtime=0)),
        "sqlite_bytes": sqlite_bytes,
        "applications": [
            row.model_dump(mode="json")
            for row in world.repo.list_result_applications(10, number)
            if row.target is not None
        ],
        "requests": [request.model_dump(mode="json") for request in world.batch.submitted],
        "messages": [
            {"kind": message.kind.value, "text": message.text} for message in world.publisher.sent
        ],
        "edits": [
            {"text": message.text, "message_id": message_id}
            for message, message_id in world.publisher.edited
        ],
        "replies": [outcome.model_dump(mode="json") for _, outcome in world.replier.replies],
    }


def test_profile():
    assert (
        Path(lexinform.__file__).resolve().parents[2]
        == Path(os.environ["DA_EXPECTED_CODE"]).resolve()
    )
    output = Path(os.environ["DA_PROJECTION_PROFILE_OUTPUT"])
    output.mkdir()
    profiles = {}
    for workers in (1, 4):
        for purpose in ("consultation_review", "commission_review"):
            world, source, intent = queued_review(purpose, workers=workers)
            submit_review(world, intent)
            world.batch.resolve()
            world.analysis.collect_batches()
            world.analysis.start_run()
            work = ReviewWork(
                memo_key=source.memo_key(10),
                purpose=purpose,
                context=source,
                source_key="source/1",
                since=world.clock.now(),
                baseline_day=world.clock.now().date(),
            )
            reviews = world.container.document_reviews(dry_run=False)
            for key in ("source/1", "source/2"):
                world.repo.save_review_work(
                    10, "3039", (work.model_copy(update={"source_key": key}),)
                )
                reviews.advance(TrackingResult(), publish=False)
                world.repo.restore(world.repo.dump())
            reviews.advance(TrackingResult(), publish=False)
            name = f"{purpose}-{workers}"
            profiles[name] = snapshot(world, output, name)
            world.repo.close()
        world = joint_world(workers=workers)
        world.run()
        world.batch.resolve()
        world.analysis.collect_batches()
        world.run()
        world.repo.restore(world.repo.dump())
        world.run()
        name = f"joint-{workers}"
        profiles[name] = snapshot(world, output, name, "1929")
        world.repo.close()
        world = World(batch=True, batch_only=True, name_cards=True, workers=workers)
        world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
        world.run()
        world.batch.resolve()
        world.run()
        world.batch.resolve()
        world.analysis.collect_batches()
        world.run()
        world.repo.restore(world.repo.dump())
        world.run()
        name = f"name-{workers}"
        profiles[name] = snapshot(world, output, name)
        world.repo.close()
        world, original = published_world(workers=workers)
        world.command("/amendments 1929")
        world.run(track=False)
        world.batch.resolve()
        world.analysis.collect_batches()
        world.repo.restore(world.repo.dump())
        world.run(track=False)
        name = f"revision-{workers}"
        profiles[name] = snapshot(world, output, name, "1929")
        assert (
            original.id is not None and len(world.repo.list_amendment_revisions(original.id)) == 1
        )
        world.repo.close()
        world = World(workers=workers)
        world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
        world.run()
        world.command("/explain 3039")
        commands_only(world)
        world.batch.resolve()
        for _ in range(2):
            world.command("/explain 3039")
            commands_only(world)
            world.repo.restore(world.repo.dump())
        name = f"explanation-{workers}"
        profiles[name] = snapshot(world, output, name)
        world.repo.close()
    (output / "profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n")
