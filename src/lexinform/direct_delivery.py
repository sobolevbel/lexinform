from lexinform.models import DeliveryPlan, Publication, PublicationKind, PublicationStatus

DIRECT_NOTICES = frozenset(
    (
        PublicationKind.ACT_PUBLISHED,
        PublicationKind.CONSULTATION_RESULTS,
        PublicationKind.IN_FORCE,
        PublicationKind.CONSULTATION_DEADLINE,
        PublicationKind.DECISION_DEADLINE,
        PublicationKind.HEARING_DEADLINE,
    )
)
DIRECT_ROOTS = DIRECT_NOTICES | {
    PublicationKind.NEW_BILL,
    PublicationKind.JOINT_BILL,
    PublicationKind.STATUS_UPDATE,
}


def validate_reader_contract(publication: Publication) -> None:
    plan = publication.delivery
    if plan is None:
        raise ValueError("The reader delivery lacks its saved payload")
    if publication.kind is PublicationKind.MESSAGE_PART and not plan.rendered_text.strip():
        raise ValueError("A continuation requires its frozen physical HTML")
    if publication.kind in DIRECT_ROOTS and (
        plan.sitting_group
        or (plan.group_text and publication.kind is not PublicationKind.STATUS_UPDATE)
    ):
        raise ValueError("A direct reader delivery cannot carry a sitting group")
    if publication.kind in DIRECT_NOTICES and plan.edit_message_id is not None:
        raise ValueError("A notice cannot replace an existing reader message")


def direct_root_plan(publication: Publication) -> DeliveryPlan:
    validate_reader_contract(publication)
    plan = publication.delivery
    assert plan is not None, "validate_reader_contract requires a saved delivery"
    if (
        publication.id is None
        or publication.kind not in DIRECT_ROOTS
        or publication.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED)
        or plan.destination != "direct"
        or not plan.bill_json
        or plan.rendered_text
        or plan.group_text
        or plan.publication_ids
        or plan.continuation_ids
        or plan.continuation_parent is not None
        or plan.continuation_root is not None
        or plan.continuation_index != 1
        or plan.digest_parts
        or plan.digest_event_ids
        or plan.absorbed_by is not None
        or plan.revision_of is not None
        or (plan.reply_to is not None and plan.reply_to <= 0)
        or (
            plan.edit_message_id is not None
            and (publication.kind is not PublicationKind.STATUS_UPDATE or plan.edit_message_id <= 0)
        )
    ):
        raise ValueError("The saved payload is not an unowned direct reader root")
    if publication.kind is PublicationKind.NEW_BILL and plan.reply_to is not None:
        raise ValueError("A first card cannot inherit a reply target")
    if publication.kind is PublicationKind.JOINT_BILL and not plan.primary_json:
        raise ValueError("The joint reply lacks its saved primary bill")
    if publication.kind is PublicationKind.STATUS_UPDATE and (
        not plan.change_json or (plan.edit_message_id is not None and plan.today is None)
    ):
        raise ValueError("The saved update lacks its change or original edit date")
    if (
        publication.kind
        in DIRECT_NOTICES
        - {
            PublicationKind.ACT_PUBLISHED,
            PublicationKind.CONSULTATION_RESULTS,
        }
        and plan.today is None
    ):
        raise ValueError("The saved reminder lacks its original date")
    if publication.kind is PublicationKind.DECISION_DEADLINE and not plan.phase_json:
        raise ValueError("The saved decision reminder lacks its phase")
    if publication.kind is PublicationKind.HEARING_DEADLINE and not plan.hearing_json:
        raise ValueError("The saved hearing reminder lacks its hearing")
    return plan
