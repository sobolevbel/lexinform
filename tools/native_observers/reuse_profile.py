import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from tests.harness import World
from tests.scenario.test_secondary_computation_history import URL, ask

import lexinform
from lexinform.models import AmendmentsRecord, JointRecord, NameRecord, SupplementRecord
from lexinform.services.analysis import Waiting


def snapshot(world, output, name):
    raw = world.repo.dump().encode()
    (output / f"{name}.sql").write_bytes(raw)
    with closing(sqlite3.connect(":memory:")) as database:
        database.executescript(raw.decode())
        assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not database.execute("PRAGMA foreign_key_check").fetchall()
        size = len(database.serialize())
    return {
        "sql_sha256": hashlib.sha256(raw).hexdigest(),
        "sql_bytes": len(raw),
        "gzip_bytes": len(gzip.compress(raw, mtime=0)),
        "sqlite_bytes": size,
        "bill": world.bill("3039").model_dump(mode="json"),
        "messages": [row.text for row in world.publisher.sent],
        "edits": [row.text for row, _ in world.publisher.edited],
    }


def test_profile():
    assert (
        Path(lexinform.__file__).resolve().parents[2]
        == Path(os.environ["DA_EXPECTED_CODE"]).resolve()
    )
    output = Path(os.environ["DA_REUSE_PROFILE_OUTPUT"])
    output.mkdir()
    profiles = {}
    for model in ("gpt-5.1", "claude-opus-5-5"):
        for workers in (1, 4):
            for kind in ("amendments", "supplement", "joint", "name"):
                world = World(
                    profile="production", workers=workers, analysis_model=model, joint_model=model
                )
                settings = world.container.settings.model_copy(
                    update={"llm_amendments_model": model, "llm_supplement_model": model}
                )
                world.container = replace(
                    world.container, settings=settings, analysis_options_override=None
                )
                world.analysis = world.container.analysis_service()
                world.pipeline = world.container.pipeline(dry_run=False)
                world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
                world.add_bill("3040", "Projekt ustawy o obywatelstwie")
                world.run(track=False)
                world.batch.resolve()
                world.run(track=False)
                world.gateway.files[URL] = b"%PDF-secondary"
                world.analysis.start_run()
                assert isinstance(ask(world, kind), Waiting)
                world.analysis.submit_queued_batches()
                request = world.batch.submitted[-1].model_dump(mode="json")
                world.batch.resolve()
                world.analysis.collect_batches()
                world.analysis.acknowledge_batch_costs()
                original = next(
                    row
                    for row in world.repo.list_computation_results(10, "3039")
                    if row.purpose == kind
                )
                assert original.provider == ("openai" if model.startswith("gpt-") else "anthropic")
                world.clock.advance(days=200)
                world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
                world.repo.restore(world.repo.dump())
                assert not world.repo.load_analysis_memo()
                world.analysis.start_run()
                answer = ask(world, kind)
                repeated = isinstance(answer, Waiting)
                paid_calls = []
                if repeated:
                    world.analysis.submit_queued_batches()
                    world.batch.resolve()
                    world.analysis.collect_batches()
                    paid_calls = [call.model_dump(mode="json") for call in world.analysis.calls]
                    world.analysis.acknowledge_batch_costs()
                    world.analysis.start_run()
                    answer = ask(world, kind)
                assert isinstance(
                    answer, (AmendmentsRecord, SupplementRecord, JointRecord, NameRecord)
                )
                assert world.repo.get_computation_result(original.result_id) == original
                name = f"{model}-{workers}-{kind}"
                record = answer.model_dump(mode="json")
                content = {
                    key: record[key]
                    for key in ("amendments", "digest", "comparison", "proposal")
                    if key in record
                }
                profiles[name] = {
                    "original_request": request,
                    "original_result": original.model_dump(mode="json"),
                    "requests": [
                        row.model_dump(mode="json")
                        for row in world.batch.submitted
                        if row.call_kind == kind
                    ],
                    "results": [
                        row.model_dump(mode="json")
                        for row in world.repo.list_computation_results(10, "3039")
                        if row.purpose == kind
                    ],
                    "paid_calls_after_cleanup": paid_calls,
                    "answer": record,
                    "content": content,
                    "snapshot": snapshot(world, output, name),
                }
                world.repo.close()
    (output / "profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n")
