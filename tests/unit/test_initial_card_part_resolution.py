import datetime as dt

import pytest

from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    failed_card_part_resolution_allowed,
)


def initial_parts() -> tuple[Publication, Publication]:
    root = Publication(
        id=1,
        term=10,
        number="3039",
        kind=PublicationKind.NEW_BILL,
        channel_id="@test",
        status=PublicationStatus.SENT,
        message_id=101,
        created_at=dt.datetime(2026, 9, 7, tzinfo=dt.UTC),
        delivery=DeliveryPlan(bill_json="{}", rendered_text="Frozen root", continuation_ids=(2,)),
    )
    child = root.model_copy(
        update={
            "id": 2,
            "kind": PublicationKind.MESSAGE_PART,
            "status": PublicationStatus.FAILED,
            "message_id": None,
            "attempts": 3,
            "ref": "message/1/2",
            "delivery": DeliveryPlan(
                bill_json="{}",
                rendered_text="Frozen continuation",
                continuation_root=1,
                continuation_parent=1,
                continuation_index=2,
            ),
        }
    )
    return root, child


@pytest.mark.parametrize(
    "invalid",
    [
        "kind",
        "status",
        "channel",
        "term",
        "number",
        "no_html",
        "edit_target",
        "root",
        "parent",
        "index",
        "membership",
        "root_status",
        "root_html",
        "root_edit",
        "root_parent",
        "root_revision",
        "confirm",
    ],
)
def test_unqualified_failed_initial_card_parts_cannot_be_recovered(invalid: str) -> None:
    root, child = initial_parts()
    assert failed_card_part_resolution_allowed(child, root, None, "retry")
    root_plan, child_plan = root.delivery, child.delivery
    assert root_plan is not None and child_plan is not None, (
        "the fixture retains exact physical plans"
    )
    action = "retry"
    if invalid == "kind":
        child = child.model_copy(update={"kind": PublicationKind.STATUS_UPDATE})
    elif invalid == "status":
        child = child.model_copy(update={"status": PublicationStatus.SENT})
    elif invalid in ("channel", "number"):
        child = child.model_copy(
            update={"channel_id" if invalid == "channel" else "number": "unrelated"}
        )
    elif invalid == "term":
        child = child.model_copy(update={"term": child.term + 1})
    elif invalid in ("no_html", "edit_target", "root", "parent", "index"):
        key, value = {
            "no_html": ("rendered_text", ""),
            "edit_target": ("edit_message_id", 900000),
            "root": ("continuation_root", 900000),
            "parent": ("continuation_parent", 900000),
            "index": ("continuation_index", 900000),
        }[invalid]
        child = child.model_copy(update={"delivery": child_plan.model_copy(update={key: value})})
    elif invalid == "root_status":
        root = root.model_copy(update={"status": PublicationStatus.UNKNOWN})
    elif invalid == "confirm":
        action = "confirm"
    else:
        key, value = {
            "membership": ("continuation_ids", (900000,)),
            "root_html": ("rendered_text", ""),
            "root_edit": ("edit_message_id", 900000),
            "root_parent": ("continuation_root", 900000),
            "root_revision": ("revision_of", 900000),
        }[invalid]
        root = root.model_copy(update={"delivery": root_plan.model_copy(update={key: value})})

    assert not failed_card_part_resolution_allowed(child, root, None, action)
