import gzip
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest
from tests.harness import World

import lexinform
from lexinform.models import BatchIntent, BillStatus
from lexinform.services.analysis import AnalysisOutcome


def test_profile():
    assert (
        Path(lexinform.__file__).resolve().parents[2]
        == Path(os.environ["DA_EXPECTED_CODE"]).resolve()
    )
    output = Path(os.environ["DA_PRIMARY_PROFILE_OUTPUT"])
    output.mkdir()
    profiles = {}
    for model in ("gpt-5.1", "claude-opus-5-5"):
        for workers in (1, 4):
            for path in ("direct", "pending"):
                for stage in ("warm", "cold", "changed", "legacy", "force"):
                    if stage == "force" and path == "pending":
                        continue
                    world = World(profile="production", workers=workers, analysis_model=model)
                    world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
                    save = world.repo.save_batch_intent

                    def legacy(
                        intent: BatchIntent, save: Callable[[BatchIntent], None] = save
                    ) -> None:
                        save(
                            intent.model_copy(
                                update={
                                    "meta": intent.meta.model_copy(update={"input_binding": None})
                                }
                            )
                        )

                    with pytest.MonkeyPatch.context() as patch:
                        if stage == "legacy":
                            patch.setattr(world.repo, "save_batch_intent", legacy)
                        world.run(track=False)
                    world.batch.resolve()
                    world.run(track=False)
                    paid = world.repo.list_computation_results(10, "3039", purpose="analysis")[0]
                    request = world.batch.submitted[0].model_dump(mode="json")
                    world.clock.advance(days=200)
                    if stage != "warm":
                        world.repo.prune_history(
                            before=world.clock.now(), memo_before=world.clock.now()
                        )
                    world.repo.restore(world.repo.dump())
                    world.repo.reset_bill(10, "3039", BillStatus.ANALYSIS_PENDING)
                    if stage == "changed":
                        world.gateway.details["3039"] = world.gateway.details["3039"].model_copy(
                            update={"title": "Inny projekt ustawy o cudzoziemcach"}
                        )
                    world.analysis.start_run()
                    if path == "direct":
                        answer = world.analysis.analyze_bill(
                            world.bill("3039"), ignore_cost_limit=stage == "force"
                        )
                        free = isinstance(answer, AnalysisOutcome)
                    else:
                        outcome = world.analysis.analyze_pending(limit=10)
                        free = outcome.analyzed == 1 and outcome.batched == 0
                    raw = world.repo.dump().encode()
                    case = f"{model}-{workers}-{path}-{stage}"
                    (output / f"{case}.sql").write_bytes(raw)
                    with closing(sqlite3.connect(":memory:")) as database:
                        database.executescript(raw.decode())
                        sqlite_bytes = len(database.serialize())
                    profiles[case] = {
                        "free": free,
                        "bill": world.bill("3039").model_dump(mode="json"),
                        "applications": [
                            row.model_dump(mode="json")
                            for row in world.repo.list_result_applications(10, "3039")
                        ],
                        "original_request": request,
                        "original_result": world.repo.get_computation_result(
                            paid.result_id
                        ).model_dump(mode="json"),
                        "requests": [row.model_dump(mode="json") for row in world.batch.submitted],
                        "intents": [
                            row.model_dump(mode="json")
                            for row in world.repo.list_queued_batch_intents()
                        ],
                        "sql_sha256": hashlib.sha256(raw).hexdigest(),
                        "sql_bytes": len(raw),
                        "gzip_bytes": len(gzip.compress(raw, mtime=0)),
                        "sqlite_bytes": sqlite_bytes,
                    }
    (output / "profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n")
    assert len(profiles) == 36
