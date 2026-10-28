from lexinform.models import (
    DeliveryPlan,
    Publication,
    PublicationKind,
    PublicationStatus,
    SittingNews,
)

SITTING_KINDS = frozenset((PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED))


def validate_sitting_contract(publication: Publication) -> None:
    plan = publication.delivery
    if plan is None or plan.edit_message_id is not None or plan.revision_of is not None:
        raise ValueError("A sitting requires a saved top-level delivery without an edit target")
    if not plan.bill_json or not plan.item_json:
        raise ValueError("The saved sitting lacks its bill or agenda item")
    if (plan.rendered_text and not plan.rendered_text.strip()) or (
        plan.sitting_group and not plan.group_text.strip()
    ):
        raise ValueError("A prepared sitting requires nonempty physical HTML")
    SittingNews.of(publication.kind, plan)


def sitting_root_plan(publication: Publication) -> DeliveryPlan:
    validate_sitting_contract(publication)
    plan = publication.delivery
    assert plan is not None, "validate_sitting_contract requires the saved sitting plan"
    if (
        publication.id is None
        or publication.kind not in SITTING_KINDS
        or publication.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED)
        or plan.destination != "direct"
        or not plan.bill_json
        or not plan.item_json
        or plan.rendered_text
        or plan.render_origin is not None
        or plan.group_text
        or plan.publication_ids
        or plan.continuation_ids
        or plan.continuation_parent is not None
        or plan.continuation_root is not None
        or plan.continuation_index != 1
        or plan.digest_parts
        or plan.digest_event_ids
        or plan.absorbed_by is not None
        or (plan.reply_to is not None and plan.reply_to <= 0)
    ):
        raise ValueError("The saved payload is not an unowned sitting root")
    return plan
