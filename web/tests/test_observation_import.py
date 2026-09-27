import datetime as dt

import pytest
from tests.fakes import make_analysis
from tests.harness import COMMITTEE_STAGES, TERM, World

from lexinform_web.ingestion.activation import activate, reference_of
from lexinform_web.ingestion.contract import ImportedBill
from lexinform_web.ingestion.models import MatterEvent
from lexinform_web.ingestion.process import project_in_process
from lexinform_web.ingestion.selectors import facts_at
from lexinform_web.matters.models import SourceIdentity

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("workers", [1, 4])
def test_observation_dump_activates_history_and_freshness_without_telegram(workers: int) -> None:
    w = World(workers=workers, llm_script={"3039": make_analysis(relevant=True, score=2)})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    first = activate(
        project_in_process(w.repo.dump().encode(), source_commit="a" * 40),
        expected=None,
        clock=w.clock,
    )
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(days=1)
    w.run()
    changed_at = w.clock.now()
    second = activate(
        project_in_process(w.repo.dump().encode(), source_commit="b" * 40),
        expected=reference_of(first.generation),
        clock=w.clock,
    )
    w.clock.advance(hours=1)
    w.tracking.check_updates()
    document = project_in_process(w.repo.dump().encode(), source_commit="c" * 40)
    third = activate(document, expected=reference_of(second.generation), clock=w.clock)

    identity = SourceIdentity.objects.get(source="sejm", scope=str(TERM), external_id="3039")
    facts = facts_at(third.generation, identity.matter_id)
    assert facts is not None and facts.content_updated_at == changed_at
    imported = ImportedBill.model_validate(facts.snapshot.payload)
    check = next(check for check in imported.checks if check.aspect == "process")
    assert check.last_success_at == changed_at + dt.timedelta(hours=1)
    assert imported.observation_mode == "full"
    assert imported.facts.stages
    assert MatterEvent.objects.count() == 1
    assert third.generation.counts["new_events"] == 0
    assert third.generation.counts["revised_events"] == 0
    assert document.telegram == () and w.publication("3039") is None
