import logging

import pytest

from lexinform.log_context import correlation_fields, current_log_fields
from tests.harness import World
from tests.scenario.test_amendments_command import published_world


@pytest.mark.parametrize("workers", [1, 4])
def test_batch_source_result_and_delivery_logs_keep_their_actual_run_and_sql_ids(
    workers: int, caplog: pytest.LogCaptureFixture
) -> None:
    world = World(profile="production", workers=workers)
    world.add_bill("4100", "Projekt ustawy o cudzoziemcach")
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")

    world.run(track=False)
    first = [correlation_fields(getattr(record, "correlation", None)) for record in caplog.records]
    caplog.clear()
    world.batch.resolve()
    world.run(track=False)
    second = [correlation_fields(getattr(record, "correlation", None)) for record in caplog.records]

    observed = world.repo.list_source_observations(10, "4100")[0]
    result = world.repo.list_computation_results(10, "4100")[0]
    publication = world.publication("4100")
    assert publication is not None, "collection publishes the retained analysis"
    assert any(
        fields.get("observation_id") == observed.observation_id
        and fields.get("selection_id") == observed.selection_id
        and fields.get("run_id") == 1
        for fields in first
    )
    assert any(fields.get("request_sha256") for fields in first)
    assert any(fields.get("artifact_sha256") for fields in first)
    assert any(
        fields.get("computation_id") == result.result_id and fields.get("run_id") == 2
        for fields in second
    )
    assert any(
        fields.get("publication_id") == publication.id
        and fields.get("delivery_status") == "sent"
        and fields.get("run_id") == 2
        for fields in second
    )
    assert all(fields["run_id"] == 2 for fields in second if fields)
    assert current_log_fields() == {}


def test_command_and_content_bindings_do_not_leak_into_the_next_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world, original = published_world()
    assert original.id is not None, "the fixture publishes the original amendment update"
    world.command("/amendments 1929")
    caplog.set_level(logging.DEBUG, logger="lexinform.adapters.sqlite_repo")

    world.run(track=False)
    command_fields = [
        correlation_fields(getattr(record, "correlation", None)) for record in caplog.records
    ]
    caplog.clear()
    world.batch.resolve()
    world.run()
    binding_fields = [
        correlation_fields(getattr(record, "correlation", None))
        for record in caplog.records
        if record.getMessage() == "local publication content binding"
    ]

    assert any(fields.get("command_id") for fields in command_fields)
    assert binding_fields and all("command_id" not in fields for fields in binding_fields)
    revision = world.repo.list_amendment_revisions(original.id)[0]
    content = world.repo.get_publication_content(revision.id or 0)
    assert content is not None, "the confirmed revision has an immutable content binding"
    assert any(fields.get("content_id") == content.content_id for fields in binding_fields)
    assert current_log_fields() == {}


def test_delivery_drain_and_failed_term_resolution_clear_the_run_context() -> None:
    world = World()
    world.gateway.outages.add("list_terms")

    failed = world.run(term=None)
    assert failed.term is None and not failed.ok
    assert any("list_terms: connection refused" in error for error in failed.errors)
    world.container.delivery_service(dry_run=False).run()

    assert current_log_fields() == {}
