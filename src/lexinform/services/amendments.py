import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    AmendmentRevisionTarget,
    Bill,
    CommandOutcome,
    DeliveryPlan,
    DigestItemMeta,
    OutcomeStatus,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    TextDocument,
    amendments_stage,
    senate_position_reported,
)
from lexinform.ports import BillRepository, SejmGateway
from lexinform.senate_documents import adoption_before_senate
from lexinform.services.analysis import AnalysisService, Waiting

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AmendmentsSource:
    """What an amendments stage is read from, with the committee's proposal and its report."""

    document: TextDocument
    proposal: str | None = None
    recommendation: TextDocument | None = None


def amendments_source(
    gateway: SejmGateway,
    analysis: AnalysisService,
    stage: Stage,
    stages: tuple[Stage, ...],
    *,
    term: int,
    titles: tuple[str, ...],
) -> AmendmentsSource | None:
    """A report on the Senate's amendments lists only votes: the resolution is read with it."""
    senate = stage if stage.stage_type == "SenatePosition" else None
    senate = senate or senate_position_reported(stage, stages)
    if senate is None:
        assert stage.report_file is not None, (
            "amendments_stage takes a committee report only with its file"
        )
        report = TextDocument(url=stage.report_file, kind="committee_amendments")
        return AmendmentsSource(report, stage.proposal)
    assert senate.print_number is not None, "a Senate position with amendments has its print"
    try:
        info = gateway.get_print(term, senate.print_number)
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        log.warning("Senate print %s unavailable: %s", senate.print_number, exc)
        return None
    document = analysis.senate_amendments_document(
        info,
        senate,
        titles=titles,
        passed_on=adoption_before_senate(stages, info.document_date or senate.date),
    )
    if document is None:
        return None
    if senate is stage:
        return AmendmentsSource(document)
    assert stage.report_file is not None, "amendments_stage takes a report only with its file"
    report = TextDocument(url=stage.report_file, kind="committee_amendments")
    return AmendmentsSource(document, stage.proposal, report)


class AmendmentsService:
    def __init__(
        self,
        repo: BillRepository,
        gateway: SejmGateway,
        analysis: AnalysisService,
        *,
        channel_id: str,
    ) -> None:
        self._repo = repo
        self._gateway = gateway
        self._analysis = analysis
        self._channel_id = channel_id

    def request(
        self, bill: Bill, *, publication_id: int | None = None, force_id: str = ""
    ) -> CommandOutcome:
        try:
            original = self._original(bill, publication_id)
            assert original.id is not None and original.delivery is not None
            revisions = self._repo.list_amendment_revisions(original.id)
            for revision in revisions:
                if revision.status in (
                    PublicationStatus.QUEUED,
                    PublicationStatus.FAILED,
                    PublicationStatus.PENDING,
                    PublicationStatus.UNKNOWN,
                ):
                    return self._delivery_outcome(bill, revision)
            if any(r.delivery is not None and r.delivery.continuation_ids for r in revisions):
                if not force_id:
                    return self._delivery_outcome(bill, revisions[0])
                raise ValueError(
                    "Исправление уже имеет продолжения; повторное редактирование недоступно"
                )
            waiting = self._pending(original.id)
            if waiting is not None:
                return self._waiting_outcome(bill, original.id, waiting)
            snapshot = Bill.model_validate_json(original.delivery.bill_json)
            if snapshot.analysis is None:
                raise ValueError("В историческом снимке сообщения нет анализа законопроекта")
            change = StatusChange.model_validate_json(original.delivery.change_json)
            stage = amendments_stage(change.new_stages)
            if stage is None:
                raise ValueError("В сообщении нет стадии с поправками")
            source = amendments_source(
                self._gateway,
                self._analysis,
                stage,
                snapshot.stages,
                term=bill.term,
                titles=(snapshot.summary.title,),
            )
            if source is None:
                raise ValueError("Полный текст поправок Сената не подтверждён по содержимому")
            saved = original.delivery
            delivery = DeliveryPlan(
                bill_json=saved.bill_json,
                change_json=saved.change_json,
                participation=saved.participation,
                attention=saved.attention,
                policy_version=saved.policy_version,
                today=saved.today
                or (original.sent_at or original.created_at)
                .astimezone(ZoneInfo("Europe/Warsaw"))
                .date(),
                reply_to=saved.reply_to,
                edit_message_id=original.message_id,
                revision_of=original.id,
                with_act=saved.with_act,
            )
            result = self._analysis.revise_amendments(
                snapshot,
                source.document,
                AmendmentRevisionTarget(
                    publication_id=original.id,
                    channel_id=self._channel_id,
                    source_url=source.document.url,
                    delivery=delivery,
                ),
                proposal=source.proposal,
                recommendation=source.recommendation,
                force_id=force_id,
            )
            if isinstance(result, Waiting):
                return self._waiting_outcome(bill, original.id, result)
            return self._delivery_outcome(bill, result)
        except ValueError as exc:
            return CommandOutcome(status=OutcomeStatus.ERROR, bill=bill, note=str(exc))

    def _original(self, bill: Bill, publication_id: int | None) -> Publication:
        original = self._repo.publication_by_id(publication_id) if publication_id else None
        if publication_id is None:
            for change in reversed(self._repo.list_status_changes(bill.term, bill.number)):
                if change.id is None or amendments_stage(change.new_stages) is None:
                    continue
                candidate = self._repo.get_update_publication(change.id, self._channel_id)
                if candidate is not None and candidate.status is PublicationStatus.SENT:
                    original = candidate
                    break
        if (
            original is None
            or original.kind is not PublicationKind.STATUS_UPDATE
            or original.status is not PublicationStatus.SENT
            or original.message_id is None
            or original.channel_id != self._channel_id
            or (original.term, original.number) != (bill.term, bill.number)
        ):
            raise ValueError("Не найдено отправленное сообщение с поправками этого законопроекта")
        plan = original.delivery
        if plan is None or not plan.bill_json or not plan.change_json:
            raise ValueError("Исторический снимок отсутствует; безопасное обновление недоступно")
        if (
            plan.update_group
            or plan.sitting_group
            or plan.continuation_ids
            or plan.absorbed_by is not None
        ):
            raise ValueError("Обновление общего или multipart-сообщения не поддерживается")
        if self._repo.digest_owner(original.id or 0) is not None:
            raise ValueError("Сообщение включено в дайджест; его редактирование не поддерживается")
        return original

    def _pending(self, publication_id: int) -> Waiting | None:
        for intent in (
            *self._repo.list_queued_batch_intents(),
            *self._repo.list_submitting_batch_intents(),
        ):
            if self._targets(intent.meta, publication_id):
                return Waiting(intent.created_at)
        for batch in self._repo.list_open_llm_batches():
            for item in self._repo.list_llm_batch_items(batch.batch_id):
                if item.consumed_at is None and self._targets(item.meta, publication_id):
                    return Waiting(batch.submitted_at)
        return None

    @staticmethod
    def _targets(meta: object, publication_id: int) -> bool:
        return (
            isinstance(meta, DigestItemMeta)
            and meta.amendment_revision is not None
            and meta.amendment_revision.publication_id == publication_id
        )

    @staticmethod
    def _waiting_outcome(bill: Bill, publication_id: int, waiting: Waiting) -> CommandOutcome:
        return CommandOutcome(
            status=OutcomeStatus.QUEUED,
            bill=bill,
            note=f"Поправки для публикации {publication_id} "
            f"ожидают batch с {waiting.since.isoformat()}. "
            "После получения результата бот обновит прежнее сообщение.",
        )

    def _delivery_outcome(self, bill: Bill, publication: Publication) -> CommandOutcome:
        if publication.status is PublicationStatus.SENT and publication.delivery is not None:
            for identity in publication.delivery.continuation_ids:
                part = self._repo.publication_by_id(identity)
                if part is not None and part.status is not PublicationStatus.SENT:
                    publication = part
                    break
        return CommandOutcome(
            status=OutcomeStatus.REFRESHED
            if publication.status is PublicationStatus.SENT
            else OutcomeStatus.QUEUED,
            bill=bill,
            note=f"Исправление поправок: {publication.status.value}. "
            f"Доставка: /delivery {publication.id}. "
            "Повторный вызов сохраняет существующий результат; force запрашивает новый разбор.",
        )
