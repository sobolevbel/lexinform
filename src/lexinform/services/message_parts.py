import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from lexinform.errors import ServiceUnavailableError
from lexinform.models import Bill, DeliveryPlan, Publication, PublicationKind, PublicationStatus
from lexinform.ports import BillRepository, Clock, Publisher, StateCheckpoint
from lexinform.services.publications import send_publication, source_context_blocked

log = logging.getLogger(__name__)


def continuation_snapshot(bill: Bill) -> str:
    return bill.model_dump_json(
        exclude={
            "analysis",
            "consultation_review",
            "act_application_review",
            "review_work",
            "rcl",
            "submission",
            "wykaz",
            "observed_process",
            "agenda",
            "stages",
        }
    )


def freeze_parts(
    repo: BillRepository,
    clock: Clock,
    publisher: Publisher,
    pub: Publication,
    text: str,
    *,
    persist: Callable[[int, DeliveryPlan], bool] | None = None,
) -> Publication:
    assert pub.id is not None and pub.delivery is not None
    if source_context_blocked(repo, pub):
        raise ValueError("The frozen source context is invalid")
    if pub.delivery.rendered_text:
        return pub
    texts = publisher.split_message(text)
    plan = pub.delivery.model_copy(update={"rendered_text": texts[0]})
    child_snapshot = continuation_snapshot(Bill.model_validate_json(plan.bill_json))
    children: list[int] = []
    parent = pub.id
    with repo.atomic():
        for index, part in enumerate(texts[1:], start=2):
            child = Publication(
                term=pub.term,
                number=pub.number,
                kind=PublicationKind.MESSAGE_PART,
                channel_id=pub.channel_id,
                status=PublicationStatus.QUEUED,
                ref=f"message/{pub.id}/{index}",
                created_at=clock.now(),
                delivery=DeliveryPlan(
                    bill_json=child_snapshot,
                    rendered_text=part,
                    continuation_parent=parent,
                    continuation_root=pub.id,
                    continuation_index=index,
                    policy_version="attention-v1",
                    held_change_ids=plan.held_change_ids if index == len(texts) else (),
                ),
            )
            parent = repo.create_publication(child)
            if persist is not None and repo.publication_by_id(parent) != child.model_copy(
                update={"id": parent}
            ):
                raise ValueError("Cannot adopt a mismatched legacy continuation")
            children.append(parent)
        plan = plan.model_copy(
            update={
                "continuation_ids": tuple(children),
                "held_change_ids": () if children else plan.held_change_ids,
            }
        )
        save = persist if persist is not None else repo.replace_unattempted_delivery
        if not save(pub.id, plan):
            raise ValueError("Cannot change an attempted message payload")
    return pub.model_copy(update={"delivery": plan})


def continuation_ready(repo: BillRepository, pub: Publication) -> Publication | None:
    assert pub.id is not None and pub.delivery is not None
    if source_context_blocked(repo, pub):
        return None
    parent = repo.publication_by_id(pub.delivery.continuation_parent or 0)
    if parent is None or parent.status is not PublicationStatus.SENT or parent.message_id is None:
        return None
    if pub.delivery.reply_to is None:
        plan = pub.delivery.model_copy(update={"reply_to": parent.message_id})
        if not repo.replace_unattempted_delivery(pub.id, plan):
            return None
        pub = pub.model_copy(update={"delivery": plan})
    return pub


@dataclass
class ContinuationResult:
    sent: int = 0
    errors: list[str] = field(default_factory=list)


def send_continuations(
    repo: BillRepository,
    clock: Clock,
    publisher: Publisher,
    root: Publication,
    attempted: set[int],
    *,
    checkpoint: StateCheckpoint | None = None,
) -> ContinuationResult:
    assert root.delivery is not None
    result = ContinuationResult()
    if source_context_blocked(repo, root):
        return result
    for identity in root.delivery.continuation_ids:
        if identity in attempted:
            break
        pub = repo.publication_by_id(identity)
        assert pub is not None
        if source_context_blocked(repo, pub):
            break
        if pub.status is PublicationStatus.SENT:
            continue
        if pub.status is not PublicationStatus.QUEUED:
            break
        ready = continuation_ready(repo, pub)
        if ready is None:
            break
        repo.mark_publication(identity, PublicationStatus.PENDING, count_attempt=False)
        attempted.add(identity)
        try:
            sent = send_publication(
                repo,
                clock,
                identity,
                partial(publisher.publish_prepared, ready),
                checkpoint=checkpoint,
            )
        except ServiceUnavailableError as exc:
            error = f"Continuation {identity}: {exc.describe()}; inspect /delivery {identity}"
            result.errors.append(error)
            log.warning("%s", error)
            break
        if sent is None:
            break
        result.sent += ready.delivery is not None and ready.delivery.edit_message_id is None
    return result
