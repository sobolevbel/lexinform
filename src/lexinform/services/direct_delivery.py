from zoneinfo import ZoneInfo

from lexinform.direct_delivery import direct_root_plan
from lexinform.models import DeliveryPlan, Publication, PublicationKind
from lexinform.ports import BillRepository, Clock, Publisher
from lexinform.services.message_parts import freeze_parts


def freeze_direct_root(
    repo: BillRepository,
    clock: Clock,
    publisher: Publisher,
    publication: Publication,
    *,
    reply_to: int | None,
) -> Publication:
    saved = direct_root_plan(publication)
    today = (
        clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date()
        if publication.kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL)
        or (publication.kind is PublicationKind.STATUS_UPDATE and saved.edit_message_id is None)
        else saved.today
    )
    candidate = publication.model_copy(
        update={
            "delivery": saved.model_copy(
                update={"today": today, "reply_to": reply_to, "render_origin": "legacy_snapshot"}
            )
        }
    )

    def persist(identity: int, plan: DeliveryPlan) -> bool:
        return identity == publication.id and repo.freeze_direct_delivery(publication, plan)

    return freeze_parts(
        repo,
        clock,
        publisher,
        candidate,
        publisher.render_delivery(candidate),
        persist=persist,
    )
