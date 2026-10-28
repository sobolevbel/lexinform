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


def legacy_notice_reply(publication: Publication) -> bool:
    plan = publication.delivery
    return (
        _legacy_snapshot(publication)
        and publication.kind
        in (
            PublicationKind.ACT_PUBLISHED,
            PublicationKind.CONSULTATION_RESULTS,
            PublicationKind.IN_FORCE,
            PublicationKind.CONSULTATION_DEADLINE,
            PublicationKind.DECISION_DEADLINE,
            PublicationKind.HEARING_DEADLINE,
        )
        and plan is not None
        and plan.edit_message_id is None
        and plan.reply_to is not None
        and plan.reply_to > 0
        and (
            publication.kind
            in (PublicationKind.ACT_PUBLISHED, PublicationKind.CONSULTATION_RESULTS)
            or plan.today is not None
        )
        and (publication.kind is not PublicationKind.DECISION_DEADLINE or bool(plan.phase_json))
        and (publication.kind is not PublicationKind.HEARING_DEADLINE or bool(plan.hearing_json))
    )


def legacy_sitting_member(publication: Publication) -> bool:
    plan = publication.delivery
    return (
        publication.id is not None
        and publication.kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED)
        and (
            publication.status is PublicationStatus.FAILED
            or (publication.status is PublicationStatus.QUEUED and not publication.attempts)
        )
        and plan is not None
        and plan.policy_version == "legacy"
        and plan.destination == "direct"
        and bool(plan.bill_json and plan.item_json)
        and plan.edit_message_id is None
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


def _legacy_status_snapshot(publication: Publication) -> bool:
    plan = publication.delivery
    return (
        _legacy_snapshot(publication)
        and publication.kind is PublicationKind.STATUS_UPDATE
        and plan is not None
        and bool(plan.change_json)
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
