from lexinform.models import Publication, PublicationKind, PublicationStatus


def legacy_status_edit(publication: Publication) -> bool:
    plan = publication.delivery
    return (
        _legacy_status_snapshot(publication)
        and plan is not None
        and plan.today is not None
        and plan.edit_message_id is not None
        and plan.edit_message_id > 0
    )


def legacy_status_reply(publication: Publication) -> bool:
    plan = publication.delivery
    return (
        _legacy_status_snapshot(publication)
        and plan is not None
        and plan.edit_message_id is None
        and (plan.reply_to is None or plan.reply_to > 0)
    )


def _legacy_status_snapshot(publication: Publication) -> bool:
    plan = publication.delivery
    return (
        publication.id is not None
        and publication.kind is PublicationKind.STATUS_UPDATE
        and publication.status is PublicationStatus.FAILED
        and plan is not None
        and plan.policy_version == "legacy"
        and plan.destination == "direct"
        and bool(plan.bill_json and plan.change_json)
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
