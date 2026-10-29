import datetime as dt
import threading

import pytest

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Attachment, ProcessDetail
from lexinform.models.senate_inspection import (
    SenateInspectionLink,
    SenateInspectionPayload,
    SenateInspectionRecord,
)
from lexinform.services import senate_documents as resolver
from lexinform.services.documents import LoadedFile, TextLoader
from tests.harness import REFERRED
from tests.scenario.test_senate_document_selection import (
    FIXTURES,
    STAGE,
    SUBJECT,
    world_with_sources,
)


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("omitted", [False, True])
def test_runtime_capture_retains_duplicate_descriptors_without_a_worker_clock(
    workers: int,
    omitted: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({"resolution.docx": text}, workers=workers)
    info = world.gateway.prints["3213"]
    first = info.attachments[0]
    world.gateway.prints["3213"] = info.model_copy(
        update={
            "attachments": (
                first,
                first.model_copy(update={"name": ("ą" * 6000 if omitted else "alias") + ".docx"}),
                Attachment(
                    print_number="foreign", name="foreign.pdf", url="https://api.test/foreign"
                ),
                Attachment(print_number="3213", name=".pdf", url="https://api.test/dotfile"),
            )
        }
    )
    caller = threading.get_ident()
    writes: list[int] = []
    clocks: list[int] = []
    save = world.repo.save_senate_inspection
    now = world.clock.now

    def record(
        value: SenateInspectionRecord,
        links: tuple[SenateInspectionLink, ...] = (),
        *,
        recorded_at: dt.datetime,
    ) -> None:
        writes.append(threading.get_ident())
        save(value, links, recorded_at=recorded_at)

    def time() -> dt.datetime:
        clocks.append(threading.get_ident())
        return now()

    monkeypatch.setattr(world.repo, "save_senate_inspection", record)
    monkeypatch.setattr(world.clock, "now", time)
    report = world.run()
    assert not report.errors and writes == [caller] and set(clocks) == {caller}
    records = tuple(world.repo.iter_senate_inspections(10, "1929"))
    assert len(records) == 1 and records[0].recorded_at == world.clock.now()
    value = records[0]
    assert (value.coverage == "omitted_over_limit") is omitted
    if isinstance(value.payload, SenateInspectionPayload):
        assert [item.inspection.reason for item in value.payload.universe] == [
            "qualified_text",
            "qualified_text",
            "foreign_print",
            "unsupported_extension",
        ]
    assert len(value.model_dump_json().encode()) <= (2048 if omitted else 8192)
    links = world.repo.list_senate_inspection_links(value.inspection_id)
    assert len(links) == 2 and links[0].selection_id is links[1].selection_id is None
    assert not world.container.senate_inspections().snapshot()[0].sources
    assert world.container.senate_inspections().snapshot()[0].record.recorded_at is None
    assert world.llm.amendment_contexts[0].text == text
    world.repo.restore(world.repo.dump())


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("outage", [False, True])
def test_tracking_joins_unconsumed_inspections_and_records_on_the_caller_in_input_order(
    workers: int,
    outage: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({"resolution.docx": text}, workers=workers)
    world.set_stages("1929", REFERRED)
    world.add_bill("2000", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    world.run()
    for number in ("1929", "2000"):
        world.set_stages(number, REFERRED + (STAGE,))
    completed = threading.Event()
    caller = threading.get_ident()
    process = world.gateway.get_process
    read = TextLoader.read
    save = world.repo.save_senate_inspection
    writes: list[tuple[str, int]] = []

    def recorded(
        value: SenateInspectionRecord,
        links: tuple[SenateInspectionLink, ...] = (),
        *,
        recorded_at: dt.datetime,
    ) -> None:
        writes.append((value.owner.number, threading.get_ident()))
        save(value, links, recorded_at=recorded_at)

    def loaded(loader: TextLoader, url: str) -> LoadedFile:
        value = read(loader, url)
        if url == "https://api.test/resolution.docx":
            completed.set()
        return value

    def detail(term: int, number: str) -> ProcessDetail:
        if outage and number == "1929" and (workers == 1 or threading.get_ident() != caller):
            if workers > 1:
                assert completed.wait(5), (
                    "The second worker completes its read before the forced outage"
                )
            raise ServiceUnavailableError("controlled outage")
        return process(term, number)

    monkeypatch.setattr(TextLoader, "read", loaded)
    monkeypatch.setattr(world.gateway, "get_process", detail)
    monkeypatch.setattr(world.repo, "save_senate_inspection", recorded)
    report = world.run()
    assert bool(report.errors) is outage
    records = tuple(world.repo.iter_senate_inspections(10, "2000"))
    assert len(records) == (1 if workers == 4 or not outage else 0)
    if outage:
        assert not world.llm.amendment_contexts
        assert writes == ([("2000", caller)] if workers == 4 else [])
    else:
        assert writes == [("1929", caller), ("2000", caller)]
        assert len(world.llm.amendment_contexts) == 2


def test_ownerless_legacy_facade_and_capture_failure_preserve_the_selected_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({"resolution.docx": text})
    info = world.gateway.prints["3213"]
    expected = world.analysis.senate_amendments_document(info, STAGE, titles=(SUBJECT,))
    assert not world.container.senate_inspections().snapshot()

    def failed(record: object) -> None:
        raise KeyboardInterrupt("capture-only cancellation")

    monkeypatch.setattr(world.container.senate_inspections(), "observe", failed)
    assert (
        world.analysis.senate_amendments_document(
            info, STAGE, titles=(SUBJECT,), owner=world.bill("1929")
        )
        == expected
    )


@pytest.mark.parametrize(
    ("kind", "completed"),
    [
        ("cancel", False),
        ("cancel", True),
        ("outage", False),
        ("outage", True),
        ("ordinary", True),
    ],
)
@pytest.mark.parametrize("omitted", [False, True])
def test_original_base_exception_survives_finally_with_exact_completed_graph(
    kind: str,
    completed: bool,
    omitted: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({"resolution.docx": text})
    info = world.gateway.prints["3213"]
    world.gateway.prints["3213"] = info.model_copy(
        update={
            "attachments": (
                *info.attachments,
                Attachment(
                    print_number="3213",
                    name=("ą" * 6000 if omitted else "later") + ".docx",
                    url="https://api.test/later.docx",
                ),
            )
        }
    )
    failure: BaseException = (
        KeyboardInterrupt("original cancellation")
        if kind == "cancel"
        else ServiceUnavailableError("original outage")
        if kind == "outage"
        else ValueError("original interpretation failure")
    )
    cause = ValueError("original cause")
    failure.__cause__ = cause

    def stop(*args: object, **kwargs: object) -> None:
        raise failure

    if completed:
        monkeypatch.setattr(resolver, "inspect_senate_resolution", stop)
    else:
        monkeypatch.setattr(TextLoader, "read", stop)
    with (
        pytest.raises(type(failure)) as captured,
        world.container.cli_document_reads().capture("amendments"),
    ):
        world.analysis.senate_amendments_document(
            world.gateway.prints["3213"], STAGE, titles=(SUBJECT,), owner=world.bill("1929")
        )
    assert captured.value is failure and failure.__cause__ is cause
    records = tuple(world.repo.iter_senate_inspections(10, "1929"))
    assert len(records) == 1 and records[0].link_count == int(completed)
    snapshots = world.container.senate_inspections().snapshot()
    assert len(snapshots) == 1 and (records[0].coverage == "omitted_over_limit") is omitted
    if isinstance(records[0].payload, SenateInspectionPayload):
        facts = records[0].payload.universe
        assert records[0].payload.result.outcome == "propagated_failure"
        assert facts[0].inspection.state == ("inspection_error" if completed else "read_error")
        assert facts[0].inspection.reason == (
            "inspection_error"
            if completed
            else "source_unavailable"
            if kind == "outage"
            else "read_error"
        )
        assert facts[1].inspection.reason == (
            "after_outage" if kind == "outage" else "after_failure"
        )
    before = world.repo.dump()
    world.repo.restore(before)
    assert world.repo.dump() == before


def test_physical_capture_failure_cannot_change_the_resolver_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = (FIXTURES / "senate_3213_resolution.txt").read_text()
    world = world_with_sources({"resolution.docx": text})
    expected = world.analysis.senate_amendments_document(
        world.gateway.prints["3213"], STAGE, titles=(SUBJECT,)
    )

    def failed(*args: object) -> None:
        raise KeyboardInterrupt("metadata-only failure")

    monkeypatch.setattr(TextLoader, "file_origins", failed)
    assert (
        world.analysis.senate_amendments_document(
            world.gateway.prints["3213"], STAGE, titles=(SUBJECT,), owner=world.bill("1929")
        )
        == expected
    )
    assert not world.container.senate_inspections().snapshot()
