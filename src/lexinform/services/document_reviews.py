import hashlib
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lexinform.commission_reviews import (
    COMMISSION_ROLES,
    commission_source_fingerprint,
    legacy_filed_commission_baseline,
)
from lexinform.errors import ServiceUnavailableError, StateCheckpointError
from lexinform.models import (
    Bill,
    DocumentReviewRecord,
    PublicationKind,
    PublicationStatus,
    ResultApplicationTarget,
    ReviewPurpose,
    ReviewSource,
    ReviewWork,
    TextDocument,
)
from lexinform.models.reviews import commission_finding_signature, review_signature
from lexinform.ports import BillRepository, Clock, TextSource
from lexinform.services.analysis import AnalysisService, Waiting
from lexinform.services.posting import Poster
from lexinform.services.result_applications import record_secondary_application
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

    def observe_commissions(self, bills: list[Bill], result: TrackingResult) -> None:
        if not self._analysis.review_enabled("commission_review"):
            return
        for before in bills:
            fresh = self._repo.get(before.term, before.number) or before
            if fresh.analysis is None:
                continue
            pending = next(
                (work for work in fresh.review_work if work.purpose == "commission_review"), None
            )
            if pending is not None and pending.context is not None:
                continue
            snapshot = fresh.model_copy(update={"analysis": before.analysis})
            sources = self._texts.supporting_documents(snapshot, "commission_review")
            remarks = {source.identity for source in sources if source.role in COMMISSION_ROLES}
            if not remarks:
                continue
            fingerprint = commission_source_fingerprint(sources)
            previous_sources = self._texts.supporting_documents(before, "commission_review")
            previous_sources += legacy_filed_commission_baseline(before, sources)
            changed = fingerprint != commission_source_fingerprint(previous_sources)
            prior = fresh.commission_review
            if prior is not None:
                known = {
                    doc.identity for doc in prior.context.documents if doc.role in COMMISSION_ROLES
                }
                changed = changed or (
                    prior.source_fingerprint != fingerprint
                    if prior.source_fingerprint
                    else known != remarks
                )
            if pending is not None:
                changed = changed or pending.source_fingerprint != fingerprint
            if changed:
                self.request(snapshot, "commission_review", result)

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
        fresh = self._repo.get(bill.term, bill.number) or bill
        source_fingerprint = ""
        if purpose == "commission_review":
            sources = self._texts.supporting_documents(bill, purpose)
            source_fingerprint = commission_source_fingerprint(sources)
            if not any(source.role in COMMISSION_ROLES for source in sources):
                return
            pending = next((work for work in fresh.review_work if work.purpose == purpose), None)
            if pending is not None and not pending.abandoned:
                if pending.context is not None:
                    return
                original = Bill.model_validate_json(pending.input_bill_json)
                bill = bill.model_copy(update={"analysis": original.analysis})
        payload = bill.model_dump_json(
            exclude={
                "last_checked_at",
                "review_work",
                "consultation_review",
                "commission_review",
                "act_application_review",
            }
        )
        key = "review-input:" + hashlib.sha256((purpose + payload).encode()).hexdigest()
        fresh = self._repo.get(bill.term, bill.number) or bill
        prior = {
            "consultation_review": fresh.consultation_review,
            "commission_review": fresh.commission_review,
            "act_application": fresh.act_application_review,
        }[purpose]
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
                    "commission_review",
                    "act_application_review",
                }
            ),
            since=self._clock.now(),
            baseline_day=day,
            source_key=key,
            source_fingerprint=source_fingerprint,
            act_eli=bill.act.eli if bill.act is not None else None,
            comparison_document=next(
                (doc for doc in prior.context.documents if doc.role == "comparison_text"), None
            )
            if purpose == "commission_review" and prior is not None
            else None,
        )
        if purpose == "commission_review":
            pending = next((item for item in fresh.review_work if item.purpose == purpose), None)
            if pending is not None:
                work = work.model_copy(
                    update={
                        "since": pending.since,
                        "baseline_day": pending.baseline_day,
                        "comparison_document": pending.comparison_document,
                    }
                )
            remaining = tuple(item for item in fresh.review_work if item.purpose != purpose)
            self._repo.save_review_work(bill.term, bill.number, (*remaining, work))
        else:
            self._repo.save_review_work(bill.term, bill.number, (*fresh.review_work, work))

    def advance(self, result: TrackingResult, *, publish: bool) -> None:
        for bill in self._repo.list_review_work():
            for work in bill.review_work:
                if work.abandoned:
                    continue
                if work.context is not None and self._analysis.review_exhausted(
                    bill.term, work.context
                ):
                    log.info("document review %s for %s abandoned", work.purpose, bill.number)
                    self._replace_work(bill, work, abandoned(work, self._clock.now()))
                    continue
                if not self._analysis.review_enabled(work.purpose):
                    continue
                try:
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
                        if work.comparison_document is not None:
                            comparison = work.comparison_document
                            sources = tuple(
                                source for source in sources if source.role != "comparison_text"
                            ) + (
                                ReviewSource(
                                    identity=comparison.identity,
                                    document=TextDocument(url=comparison.url, kind="rcl"),
                                    role="comparison_text",
                                    snapshot=comparison,
                                ),
                            )
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
                except StateCheckpointError:
                    raise
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
                PublicationKind.COMMISSION_REVIEW,
                PublicationKind.ACT_APPLICATION,
                PublicationKind.EFFECTIVE_RULES,
            ):
                continue
            if self._poster.delivery_paused(pub):
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
                    PublicationKind.COMMISSION_REVIEW: "commission_reviews_posted",
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
        previous = {
            "consultation_review": fresh.consultation_review,
            "commission_review": fresh.commission_review,
            "act_application": fresh.act_application_review,
        }[purpose]
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
                "source_fingerprint": work.source_fingerprint,
                "checked_at": self._clock.now(),
                "canonical_dates": tuple(sorted(canonical)),
            }
        )
        key = {
            "consultation_review": "consultation_review",
            "commission_review": "commission_review",
            "act_application": "act_application_review",
        }[purpose]
        snapshot = fresh.model_copy(update={key: record})
        new_findings = record.review.commission_findings
        if purpose == "commission_review" and previous is not None:
            known_findings = {
                commission_finding_signature(item, previous.context, authorities=record.context)
                for item in previous.review.commission_findings
            }
            new_findings = tuple(
                item
                for item in new_findings
                if commission_finding_signature(item, record.context) not in known_findings
            )
            message_record = record.model_copy(
                update={
                    "review": record.review.model_copy(update={"commission_findings": new_findings})
                }
            )
            snapshot = snapshot.model_copy(update={key: message_record})
        kind = {
            "consultation_review": PublicationKind.CONSULTATION_REVIEW,
            "commission_review": PublicationKind.COMMISSION_REVIEW,
            "act_application": PublicationKind.ACT_APPLICATION,
        }[purpose]
        with self._repo.atomic():
            self._repo.save_document_review(bill.term, bill.number, record)
            record_secondary_application(
                self._repo,
                bill.term,
                bill.number,
                record.computation_id,
                target=ResultApplicationTarget(
                    kind="document_review", ref=f"{purpose}/{work.source_key or work.memo_key}"
                ),
                now=self._clock.now(),
            )
            meaningful = bool(
                record.review.consultation_findings or new_findings or record.review.effective_rules
            )
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


def abandoned(work: ReviewWork, now: datetime) -> ReviewWork:
    """The review's batch failed at every allowed submission: its documents are not kept."""
    return work.model_copy(
        update={
            "context": None,
            "input_bill_json": "",
            "checked_at": now,
            "limitation": "Review failed at every batch submission; not retried.",
        }
    )
