import pytest

from lexinform.models import BillStatus, process_summary
from tests.harness import RCL, SINCE, TERM, World, rcl_project


@pytest.mark.parametrize("path", ["discovery", "lookup"])
@pytest.mark.parametrize(
    "case", ["local", "remote", "missing", "incomplete", "outage", "skipped", "linked"]
)
def test_print_predecessor_resolution(path: str, case: str) -> None:
    w = World()
    rm_number = "RM-0610-7-26"
    project = rcl_project(rm_number=rm_number if case == "local" else None)
    if case != "missing":
        w.repo.upsert_summary(process_summary(project, term=TERM), now=w.clock.now())
        if case != "incomplete":
            w.repo.save_rcl(TERM, RCL, project)
        status = {
            "skipped": BillStatus.SKIPPED_PREFILTER,
            "linked": BillStatus.LINKED,
        }.get(case, BillStatus.ANALYZED)
        w.repo.set_status(TERM, RCL, status)
    w.rcl.rm_numbers[rm_number] = project.id
    if case == "outage":
        w.rcl.outages.add("resolve_project_id")
    w.add_bill("2172", "Projekt ustawy o cudzoziemcach")
    w.touch("2172", w.clock.now(), rcl_num=rm_number)

    if path == "discovery":
        w.discovery.discover(TERM, SINCE)
    else:
        w.container.bill_lookup().load("2172")

    follows = case in {"local", "remote"}
    printed = w.repo.get(TERM, "2172")
    if path == "discovery" and follows:
        assert printed is None
        stored_project = w.bill(RCL).rcl
        assert stored_project is not None
        assert stored_project.print_number == "2172"
    else:
        assert printed is not None
        assert (printed.linked_number == RCL) is follows
    assert (f"resolve_project_id:{rm_number}" in w.rcl.calls) is (case != "local")
