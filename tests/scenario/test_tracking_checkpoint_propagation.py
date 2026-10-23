import pytest

from lexinform.errors import StateCheckpointError
from lexinform.models import Bill, PublicationKind, StatusChange
from lexinform.services.posting import Poster, Told
from lexinform.services.tracking.linking import Linker
from lexinform.services.tracking.wykaz import WykazLinker
from lexinform.services.tracking_result import TrackingResult
from tests.harness import (
    COMMITTEE_STAGES,
    RCL,
    RCL_ID,
    RPW,
    TERM,
    World,
    rcl_project,
    submission,
    wykaz_entry,
)

FAILURE = "injected tracking persistence failure"
TITLE = "Projekt ustawy o cudzoziemcach"


def numbered_world() -> World:
    world = World()
    for number in ("3039", "3040"):
        world.add_bill(number, TITLE)
    world.run()
    for number in ("3039", "3040"):
        world.set_stages(number, COMMITTEE_STAGES)
    return world


def reject_tell(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def tell(
        poster: Poster,
        bill: Bill,
        change: StatusChange,
        result: TrackingResult,
        *,
        publish: bool,
        with_act: bool = False,
    ) -> Told:
        calls.append(bill.number)
        raise StateCheckpointError(FAILURE)

    monkeypatch.setattr(Poster, "tell", tell)
    return calls


def assert_run_stops(world: World, calls: list[str], first: str) -> None:
    sent = len(world.publisher.sent)
    edited = len(world.publisher.edited)

    with pytest.raises(StateCheckpointError, match=FAILURE):
        world.run(discover=False, analyze=False, max_publish=0)

    assert calls == [first]
    assert len(world.publisher.sent) == sent
    assert len(world.publisher.edited) == edited
    report = world.notifier.calls[-1][0]
    assert report.finished_at is not None
    assert report.errors == [f"state persistence failed: {FAILURE}"]
    assert "digest" not in report.phase_seconds


def test_process_posting_checkpoint_failure_stops_the_next_bill_and_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = numbered_world()
    calls = reject_tell(monkeypatch)

    assert_run_stops(world, calls, "3039")

    assert world.publication("3040", PublicationKind.STATUS_UPDATE) is None


def test_a_per_bill_posting_failure_still_allows_the_next_bill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = numbered_world()
    original = Poster.tell
    calls: list[str] = []

    def tell(
        poster: Poster,
        bill: Bill,
        change: StatusChange,
        result: TrackingResult,
        *,
        publish: bool,
        with_act: bool = False,
    ) -> Told:
        calls.append(bill.number)
        if bill.number == "3039":
            raise RuntimeError("this bill cannot be rendered")
        return original(poster, bill, change, result, publish=publish, with_act=with_act)

    monkeypatch.setattr(Poster, "tell", tell)

    report = world.run(discover=False, analyze=False, max_publish=0)

    assert calls == ["3039", "3040"]
    assert report.updates == 1
    assert report.errors == ["1 status update(s) failed"]


def test_pre_print_checkpoint_failure_stops_the_next_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    second = "RPW/29076/2026"
    world.gateway.submissions.extend((submission(), submission(number=second)))
    world.run()
    world.gateway.submissions[:] = [
        submission(number=number, status="WITHDRAWN") for number in (RPW, second)
    ]
    calls = reject_tell(monkeypatch)

    assert_run_stops(world, calls, RPW)

    assert world.publication(second, PublicationKind.STATUS_UPDATE) is None


def test_rcl_link_checkpoint_failure_stops_the_next_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    for index in range(2):
        world.add_rcl_project(rcl_project(id=RCL_ID + index, wykaz_number=f"UC{164 + index}"))
    world.run()
    for index in range(2):
        number = f"RCL/{RCL_ID + index}"
        project = world.bill(number).rcl
        assert project is not None, "RCL discovery saves the project on each followed bill"
        world.repo.save_rcl(
            TERM, number, project.model_copy(update={"print_number": str(3100 + index)})
        )
    calls: list[str] = []

    def link(
        linker: Linker,
        bill: Bill,
        print_number: str,
        result: TrackingResult,
        *,
        publish: bool,
    ) -> None:
        calls.append(bill.number)
        raise StateCheckpointError(FAILURE)

    monkeypatch.setattr(Linker, "link", link)

    assert_run_stops(world, calls, RCL)

    assert world.repo.get(TERM, "3101") is None


def test_wykaz_link_checkpoint_failure_stops_the_next_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    for number in ("UD408", "UD409"):
        world.add_wykaz_entry(wykaz_entry(number=number))
    world.run()
    for index, number in enumerate(("WPL/UD408", "WPL/UD409")):
        entry = world.bill(number).wykaz
        assert entry is not None, "wykaz discovery saves the entry on each followed plan"
        world.repo.save_wykaz(
            TERM, number, entry.model_copy(update={"rcl_project_id": RCL_ID + index})
        )
    calls: list[str] = []

    def link(
        linker: WykazLinker,
        bill: Bill,
        project_id: int,
        result: TrackingResult,
        *,
        publish: bool,
    ) -> None:
        calls.append(bill.number)
        raise StateCheckpointError(FAILURE)

    monkeypatch.setattr(WykazLinker, "link", link)

    assert_run_stops(world, calls, "WPL/UD408")

    assert world.repo.get(TERM, f"RCL/{RCL_ID + 1}") is None


def test_refresh_checkpoint_failure_does_not_acknowledge_or_run_later_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = numbered_world()
    incoming = world.command("/refresh 3039")
    later = world.command("/status")
    calls = reject_tell(monkeypatch)

    assert_run_stops(world, calls, "3039")

    state = world.repo.command_state(incoming.update_id)
    assert state is not None, "the command is recorded before its tracking effect"
    assert state.executed_at is None and state.handled_at is None
    assert world.repo.command_state(later.update_id) is None
    assert world.inbox.pending() == [incoming, later]
    assert not world.replier.replies
