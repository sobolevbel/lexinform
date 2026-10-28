from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    Publication,
    PublicationKind,
    PublicationStatus,
    week_bounds,
)


def legacy_digest_root(publication: Publication) -> bool:
    if (
        publication.id is None
        or publication.term != DIGEST_TERM
        or publication.number != DIGEST_NUMBER
        or publication.kind is not PublicationKind.DIGEST
        or publication.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED)
        or publication.ref is None
    ):
        return False
    try:
        week_bounds(publication.ref)
    except ValueError:
        return False
    plan = publication.delivery
    return plan is None or (
        not plan.bill_json
        and not plan.change_json
        and plan.policy_version in ("legacy", "attention-v1")
        and plan.destination == "direct"
        and plan.digest_ref in ("", publication.ref)
        and not plan.digest_parts
        and not plan.approve_label
        and not plan.group_text
        and not plan.publication_ids
        and not plan.continuation_ids
        and plan.continuation_parent is None
        and plan.continuation_root is None
        and plan.continuation_index == 1
        and plan.reply_to is None
        and plan.edit_message_id is None
        and plan.revision_of is None
        and plan.absorbed_by is None
        and plan.participation is None
        and plan.schedule is None
        and plan.wait_until is None
        and not plan.held_change_ids
        and not plan.effective_rule_refs
        and not plan.with_act
    )


def legacy_status_reply(publication: Publication) -> bool:
    plan = publication.delivery
    return (
        _legacy_snapshot(publication)
        and publication.kind is PublicationKind.STATUS_UPDATE
        and plan is not None
        and bool(plan.change_json)
        and plan.edit_message_id is None
        and (plan.reply_to is None or plan.reply_to > 0)
    )


def _legacy_snapshot(publication: Publication) -> bool:
    plan = publication.delivery
    return (
        publication.id is not None
        and publication.status is PublicationStatus.FAILED
        and plan is not None
        and plan.policy_version == "legacy"
        and plan.destination == "direct"
        and bool(plan.bill_json)
        and plan.participation is None
        and not plan.rendered_text
        and plan.render_origin is None
        and not plan.group_text
        and not plan.publication_ids
        and not plan.continuation_ids
        and plan.continuation_parent is None
        and plan.continuation_root is None
        and plan.continuation_index == 1
        and not plan.digest_parts
        and not plan.digest_event_ids
        and plan.absorbed_by is None
        and plan.revision_of is None
    )
