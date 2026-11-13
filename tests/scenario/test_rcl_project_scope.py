"""Non-statute RCL projects stop before enrollment, document reads and model calls."""

import pytest

from lexinform.models import BillStatus, OutcomeStatus, RunMode, process_summary
from tests.harness import RCL, RCL_ID, World, rcl_project

REGULATION = (
    "projekt rozporządzenia Ministra Spraw Zagranicznych zmieniającego rozporządzenie "
    "w sprawie obniżonych opłat konsularnych"
)


@pytest.mark.parametrize("command", ["analyze", "analyze_force", "explain"])
@pytest.mark.parametrize("stored", [False, True])
def test_non_bill_command_is_explained_without_force_or_paid_work(
    command: str, stored: bool
) -> None:
    world = World()
    project = rcl_project(title=REGULATION)
    world.add_rcl_project(project)
    if stored:
        summary = process_summary(rcl_project(), term=10).model_copy(update={"title": REGULATION})
        world.repo.upsert_summary(summary, now=world.clock.now())
        world.repo.save_rcl(10, RCL, project)
        world.repo.set_status(10, RCL, BillStatus.DOCUMENT_UNSUPPORTED, reason="legacy rejection")
    verb = "analyze" if command == "analyze_force" else command
    suffix = " force" if command == "analyze_force" else ""
    world.command(f"/{verb} https://legislacja.rcl.gov.pl/projekt/{RCL_ID}{suffix}")

    report = world.run(
        mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0
    )

    ((_, outcome),) = world.replier.replies
    assert outcome.status is OutcomeStatus.SKIPPED
    assert "rozporządzenie" in outcome.note and "ustawa" in outcome.note
    assert "не поддерживается" in outcome.note
    assert "force" not in outcome.note
    assert (report.commands_handled, report.commands_failed) == (1, 0)
    assert world.llm.contexts == [] and world.publisher.new_bills == []
    assert not world.repo.list_open_llm_batches()
    assert not any(call.startswith(("get_stage:", "download:")) for call in world.rcl.calls)
    if stored:
        assert world.bill(RCL).status is BillStatus.DOCUMENT_UNSUPPORTED
    else:
        assert world.repo.get(10, RCL) is None


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("listing_names_statute", [False, True])
def test_listing_with_a_non_bill_does_not_enroll_it_or_fail_discovery(
    workers: int, listing_names_statute: bool
) -> None:
    world = World(workers=workers)
    world.add_rcl_project(rcl_project(title=REGULATION))
    if listing_names_statute:
        world.rcl.listing[0] = world.rcl.listing[0].model_copy(
            update={"title": "Projekt ustawy o cudzoziemcach"}
        )
    world.add_rcl_project(rcl_project(id=2))

    report = world.run(track=False)

    assert report.rcl_discovered == 1
    assert report.ok
    assert world.repo.get(10, RCL) is None
    assert [bill.number for bill, _ in world.publisher.new_bills] == ["RCL/2"]
    assert not any(call.startswith(f"get_stage:{RCL_ID}/") for call in world.rcl.calls)


@pytest.mark.parametrize("command", ["/unskip", "/reset"])
def test_retry_cannot_requeue_a_saved_non_bill(command: str) -> None:
    world = World()
    project = rcl_project(title=REGULATION)
    summary = process_summary(rcl_project(), term=10).model_copy(update={"title": REGULATION})
    world.repo.upsert_summary(summary, now=world.clock.now())
    world.repo.save_rcl(10, RCL, project)
    world.repo.set_status(10, RCL, BillStatus.DOCUMENT_UNSUPPORTED, reason="legacy rejection")
    world.command(f"{command} {RCL}")

    world.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)

    ((_, outcome),) = world.replier.replies
    assert outcome.status is OutcomeStatus.SKIPPED and "rozporządzenie" in outcome.note
    assert world.bill(RCL).status is BillStatus.DOCUMENT_UNSUPPORTED
    assert world.rcl.calls == [] and world.llm.contexts == []
