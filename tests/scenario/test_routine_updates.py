"""A step that gives the reader nothing new goes to the digest, not to the channel."""

import datetime as dt
from typing import Literal

import pytest

from lexinform.models import PublicationKind, PublicationStatus, Stage
from tests.harness import COMMITTEE_STAGES, RCL, START, World, rcl_project, rcl_stage

DAY = dt.date(2026, 9, 10)
REPORT = Stage(
    stage_name="Praca w komisjach po I czytaniu",
    stage_type="CommitteeWork",
    date=DAY,
    children=(
        Stage(
            stage_name="Sprawozdanie komisji",
            stage_type="CommitteeReport",
            date=DAY,
            print_number="3100",
            proposal="uchwalić projekt ustawy bez poprawek",
        ),
    ),
)
TO_PRESIDENT = Stage(
    stage_name="Ustawę przekazano Prezydentowi do podpisu", stage_type="ToPresident", date=DAY
)
SIGNED = Stage(stage_name="Prezydent podpisał ustawę", stage_type="PresidentSignature", date=DAY)


@pytest.mark.parametrize(
    ("before", "after", "destination"),
    [
        (START, COMMITTEE_STAGES, "direct"),  # a committee to write to
        (COMMITTEE_STAGES, (*COMMITTEE_STAGES, REPORT), "digest_only"),
        (COMMITTEE_STAGES, (*COMMITTEE_STAGES, TO_PRESIDENT), "digest_only"),
        (COMMITTEE_STAGES, (*COMMITTEE_STAGES, TO_PRESIDENT, SIGNED), "direct"),
    ],
    ids=["referral", "committee_report", "to_president", "signed"],
)
def test_only_a_step_with_something_new_for_the_reader_is_posted_on_its_own(
    before: tuple[Stage, ...], after: tuple[Stage, ...], destination: str
) -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=before)
    w.run()
    w.set_stages("3039", after)
    w.clock.advance(days=1)

    w.run()

    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None
    assert pub.delivery.destination == destination
    assert bool(w.publisher.texts(PublicationKind.STATUS_UPDATE)) is (destination == "direct")


@pytest.mark.parametrize("mode", ["on", "shadow", "off"])
def test_the_hand_over_to_the_sejm_goes_to_the_digest(
    mode: Literal["on", "shadow", "off"],
) -> None:
    """UDER106 (21 Oct 2026): «направлен в Сейм» with «пока ничего» was a message of its own."""
    w = World(reader_attention=mode)
    project = w.add_rcl_project(rcl_project(consultation=None))
    w.run()
    w.clock.advance(days=30)  # the consultation of 8 Sept is long over
    w.rcl.put(
        project.model_copy(
            update={
                "modified": DAY,
                "stages": (
                    *project.stages[:6],
                    rcl_stage(14, "Skierowanie projektu ustawy do Sejmu", "active", modified=DAY),
                ),
                "rm_number": "RM-0610-139-26",
            }
        )
    )

    w.run()

    pub = w.publication(RCL, PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None
    assert pub.delivery.destination == ("digest_only" if mode == "on" else "direct")
    if mode != "off":
        assert pub.delivery.destination_reason == "routine_step_without_reader_action"
    if mode == "on":
        assert pub.status is PublicationStatus.QUEUED and pub.attempts == 0
        assert not w.publisher.texts(PublicationKind.STATUS_UPDATE)


def test_an_rcl_project_reaching_its_consultation_is_still_posted() -> None:
    w = World(reader_attention="on")
    early = rcl_project(
        consultation=None,
        stages=(
            rcl_stage(2, "Uzgodnienia", "active"),
            rcl_stage(3, "Konsultacje publiczne", "not_started", modified=None),
        ),
    )
    w.add_rcl_project(early)
    w.run()
    w.clock.advance(days=1)
    w.rcl.put(
        early.model_copy(
            update={
                "modified": DAY,
                "stages": (
                    rcl_stage(2, "Uzgodnienia"),
                    rcl_stage(3, "Konsultacje publiczne", "active", modified=DAY),
                ),
            }
        )
    )

    w.run()

    pub = w.publication(RCL, PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None
    assert pub.delivery.destination == "direct"
