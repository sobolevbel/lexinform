import hashlib
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    DocumentReviewRecord,
    PublicationKind,
    PublicationStatus,
    ReviewPurpose,
    ReviewWork,
)
from lexinform.models.reviews import review_signature
from lexinform.ports import BillRepository, Clock, TextSource
from lexinform.services.analysis import AnalysisService, Waiting
from lexinform.services.posting import Poster
from lexinform.services.tracking_result import TrackingResult

log = logging.getLogger(__name__)


class DocumentReviews:
    def __init__(
        self,
        repo: BillRepository,
        texts: TextSource,
        analysis: AnalysisService,
        poster: Poster,
        clock: Clock,
        *,
        channel_id: str,
        max_attempts: int,
    ) -> None:
        self._repo = repo
        self._texts = texts
        self._analysis = analysis
        self._poster = poster
        self._clock = clock
        self._channel_id = channel_id
        self._max_attempts = max_attempts

    def request(
        self,
        bill: Bill,
        purpose: ReviewPurpose,
        result: TrackingResult,
        *,
        force_check: bool = False,
    ) -> None:
        if not self._analysis.review_enabled(purpose):
            return
        payload = bill.model_dump_json(
            exclude={
                "last_checked_at",
                "review_work",
                "consultation_review",
                "act_application_review",
            }
        )
        key = "review-input:" + hashlib.sha256((purpose + payload).encode()).hexdigest()
        fresh = self._repo.get(bill.term, bill.number) or bill
        prior = (
            fresh.consultation_review
            if purpose == "consultation_review"
            else fresh.act_application_review
        )
        if prior is not None and prior.source_key == key and not force_check:
            return
        if any(work.memo_key == key or work.source_key == key for work in fresh.review_work):
            return
        day = self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date()
        work = ReviewWork(
            memo_key=key,
            purpose=purpose,
            input_bill_json=bill.model_dump_json(
                exclude={
                    "review_work",
                    "consultation_review",
                    "act_application_review",
                }
            ),
            since=self._clock.now(),
            baseline_day=day,
            source_key=key,
            act_eli=bill.act.eli if bill.act is not None else None,
        )
        self._repo.save_review_work(bill.term, bill.number, (*fresh.review_work, work))

    def advance(self, result: TrackingResult, *, publish: bool) -> None:
        for bill in self._repo.list_review_work():
            for work in bill.review_work:
                if work.purpose == "act_application":
                    continue
                try:
                    if not self._analysis.review_enabled(work.purpose) and work.context is None:
                        continue
                    if (
                        work.checked_at
                        and work.context is None
                        and (self._clock.now() < work.checked_at + timedelta(days=1))
                    ):
                        continue
                    context = work.context
                    if context is None:
                        snapshot = Bill.model_validate_json(work.input_bill_json)
                        sources = self._texts.supporting_documents(snapshot, work.purpose)
                        context = self._analysis.review_context(snapshot, work.purpose, sources)
                        if context is None:
                            self._replace_work(
                                bill,
                                work,
                                work.model_copy(
                                    update={
                                        "checked_at": self._clock.now(),
                                        "limitation": (
                                            "Official documents unavailable or unreadable;"
                                            " notice remains valid."
                                        ),
                                    }
                                ),
                            )
                            continue
                        fresh = self._repo.get(bill.term, bill.number) or bill
                        input_key = work.memo_key
                        work = work.model_copy(
                            update={
                                "context": context,
                                "memo_key": context.memo_key(bill.term),
                                "input_bill_json": "",
                            }
                        )
                        self._repo.save_review_work(
                            bill.term,
                            bill.number,
                            tuple(
                                work if item.memo_key == input_key else item
                                for item in fresh.review_work
                            ),
                        )
                    record = self._analysis.request_document_review(bill, context)
                    if isinstance(record, Waiting):
                        result.waiting += 1
                        continue
                    self._apply(bill, work, record)
                except ServiceUnavailableError as exc:
                    result.partial_errors.append(f"document reviews: {exc.describe()}")
                    return
                except Exception as exc:
                    log.warning("document review for %s remains pending: %s", bill.number, exc)
                    result.partial_errors.append(f"document reviews: {type(exc).__name__}: {exc}")
        if not publish:
            return
        for pub in self._repo.list_due_deliveries(
            self._channel_id, max_attempts=self._max_attempts
        ):
            if pub.kind not in (
                PublicationKind.CONSULTATION_REVIEW,
                PublicationKind.ACT_APPLICATION,
                PublicationKind.EFFECTIVE_RULES,
            ):
                continue
            try:
                sent = self._poster.deliver(pub)
                fresh_pub = self._repo.publication_by_id(pub.id) if pub.id is not None else None
                if fresh_pub is not None and (
                    fresh_pub.status is PublicationStatus.SKIPPED
                    or (
                        not sent
                        and fresh_pub.status is PublicationStatus.QUEUED
                        and fresh_pub.attempts == 0
                    )
                ):
                    continue
                counter = {
                    PublicationKind.CONSULTATION_REVIEW: "consultation_reviews_posted",
                    PublicationKind.ACT_APPLICATION: "act_reviews_posted",
                    PublicationKind.EFFECTIVE_RULES: "effective_rule_messages",
                }[pub.kind]
                if sent:
                    setattr(result, counter, getattr(result, counter) + 1)
                else:
                    result.failed += 1
            except ServiceUnavailableError as exc:
                result.abort(exc, failed=True)
                return

    def _apply(self, bill: Bill, work: ReviewWork, record: DocumentReviewRecord) -> None:
        purpose = record.review.purpose
        if purpose == "act_application" and (
            bill.act is None or bill.act.eli != record.context.act_eli
        ):
            self._replace_work(bill, work, None)
            return
        fresh = self._repo.get(bill.term, bill.number) or bill
        previous = (
            fresh.consultation_review
            if purpose == "consultation_review"
            else fresh.act_application_review
        )
        baseline = (
            previous.baseline_day
            if previous is not None and previous.context.act_eli == record.context.act_eli
            else work.baseline_day
        )
        same_scope = previous is not None and previous.context.act_eli == record.context.act_eli
        canonical = set(previous.canonical_dates if same_scope and previous is not None else ())
        for context in (
            previous.context if same_scope and previous is not None else None,
            record.context,
        ):
            if context is not None and context.canonical_date is not None:
                canonical.add(context.canonical_date)
        record = record.model_copy(
            update={
                "baseline_day": baseline,
                "source_key": work.source_key,
                "checked_at": self._clock.now(),
                "canonical_dates": tuple(sorted(canonical)),
            }
        )
        key = (
            "consultation_review" if purpose == "consultation_review" else "act_application_review"
        )
        snapshot = fresh.model_copy(update={key: record})
        kind = (
            PublicationKind.CONSULTATION_REVIEW
            if purpose == "consultation_review"
            else PublicationKind.ACT_APPLICATION
        )
        with self._repo.atomic():
            self._repo.save_document_review(bill.term, bill.number, record)
            meaningful = bool(record.review.consultation_findings or record.review.effective_rules)
            if meaningful and (
                previous is None
                or review_signature(previous.review) != review_signature(record.review)
            ):
                self._poster.prepare_message(
                    snapshot,
                    kind,
                    ref=work.memo_key,
                    today=self._clock.now().astimezone(ZoneInfo("Europe/Warsaw")).date(),
                )
            self._repo.save_review_work(
                bill.term,
                bill.number,
                tuple(item for item in fresh.review_work if item.memo_key != work.memo_key),
            )

    def _replace_work(self, bill: Bill, old: ReviewWork, new: ReviewWork | None) -> None:
        fresh = self._repo.get(bill.term, bill.number) or bill
        remaining: list[ReviewWork] = []
        for item in fresh.review_work:
            if item.memo_key != old.memo_key:
                remaining.append(item)
            elif new is not None:
                remaining.append(new)
        self._repo.save_review_work(bill.term, bill.number, tuple(remaining))
