"""Detects new legislative stages and new bill texts for published bills, and posts updates.

Every update is posted as a reply to the original card and always restates the current summary.
When the text of the bill changed (committee report with amendments, text after the 3rd reading,
updated print) the bill is re-analysed first and the update also lists what changed.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from lexinform.concurrency import fan_out
from lexinform.errors import ServiceUnavailableError
from lexinform.models import (
    Bill,
    PrintInfo,
    ProcessDetail,
    Publication,
    PublicationKind,
    PublicationStatus,
    Stage,
    StatusChange,
    TokenUsage,
    add_usage,
    aggregate_clubs,
    diff_stages,
    stage_fingerprint,
)
from lexinform.ports import BillRepository, Clock, EliGateway, Publisher, SejmGateway
from lexinform.services.analysis import AnalysisService

log = logging.getLogger(__name__)


@dataclass
class TrackingResult:
    checked: int = 0
    changed: int = 0
    linked: int = 0
    acts_published: int = 0
    in_force_posted: int = 0
    reanalyzed: int = 0
    published: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage: dict[str, TokenUsage] = field(default_factory=dict)  # per model
    fatal_error: str | None = None


class StatusTrackingService:
    def __init__(
        self,
        gateway: SejmGateway,
        repo: BillRepository,
        publisher: Publisher,
        clock: Clock,
        *,
        channel_id: str,
        analysis: AnalysisService | None = None,
        eli: EliGateway | None = None,
        closed_grace_days: int = 30,
        passed_max_days: int = 180,
        max_publish_attempts: int = 3,
        club_breakdown: bool = True,
        in_force_reminders: bool = True,
        local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
        workers: int = 1,
    ) -> None:
        self._gateway = gateway
        self._repo = repo
        self._publisher = publisher
        self._clock = clock
        self._channel_id = channel_id
        self._analysis = analysis
        self._eli = eli
        self._closed_grace_days = closed_grace_days
        self._passed_max_days = passed_max_days
        self._in_force_reminders = in_force_reminders
        self._local_tz = local_tz
        self._max_publish_attempts = max_publish_attempts
        self._club_breakdown = club_breakdown
        self._workers = workers
        self._committee_names: dict[str, str] = {}

    def check_updates(self, term: int, *, publish: bool = True) -> TrackingResult:
        result = TrackingResult()
        now = self._clock.now()
        if publish and not self._retry_failed(term, result):
            return result
        if not self._reconcile_pre_print(term, result, publish=publish):
            return result
        tracked = self._repo.list_tracked(
            term,
            self._channel_id,
            closed_grace_days=self._closed_grace_days,
            passed_max_days=self._passed_max_days,
            now=now,
        )
        # Pre-print bills have no legislative process yet: _reconcile_pre_print handles them.
        followed = [bill for bill in tracked if not bill.is_pre_print]
        # The API lookups run in parallel; detection and posting stay sequential, in order.
        for outcome in fan_out(followed, self._fetch, workers=self._workers):
            bill = outcome.item
            result.checked += 1
            try:
                detail, print_info = outcome.result()
                change = self._detect(bill, detail, print_info, result)
            except ServiceUnavailableError as exc:
                result.fatal_error = exc.describe()
                log.error("aborting tracking phase: %s", result.fatal_error)
                break
            except Exception as exc:
                result.failed += 1
                log.exception("tracking druk %s failed: %s", bill.number, exc)
                continue
            if change is not None:
                result.changed += 1
            try:
                if change is not None and publish:
                    self._count(self._publish(bill, change), result)
                self._check_act(bill, detail, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.failed += 1
                result.fatal_error = exc.describe()
                log.error("aborting tracking phase: %s", result.fatal_error)
                break
        if result.fatal_error is None and publish and self._in_force_reminders:
            self._remind_in_force(term, result)
        log.info(
            "tracking: checked=%d changed=%d reanalyzed=%d published=%d failed=%d",
            result.checked,
            result.changed,
            result.reanalyzed,
            result.published,
            result.failed,
        )
        return result

    def _retry_failed(self, term: int, result: TrackingResult) -> bool:
        """Re-send status updates whose post failed earlier. False if Telegram is down."""
        for change in self._repo.list_failed_status_changes(
            term, self._channel_id, max_attempts=self._max_publish_attempts
        ):
            bill = self._repo.get(change.term, change.number)
            if bill is None:
                continue
            log.info("retrying status update for druk %s", bill.number)
            try:
                ok = self._publish(bill, change)
            except ServiceUnavailableError as exc:
                result.failed += 1
                result.fatal_error = exc.describe()
                log.error("aborting tracking phase: %s", result.fatal_error)
                return False
            if ok:
                result.published += 1
            else:
                result.failed += 1
        return True

    # ------------------------------------------------------------------ pre-print bills

    def _reconcile_pre_print(self, term: int, result: TrackingResult, *, publish: bool) -> bool:
        """Follow bills that had no print number: link them to their print, or notice withdrawal.

        Returns False when the phase must stop (an external system is down).
        """
        pending = self._repo.list_pre_print(term)
        if not pending:
            return True
        earliest = min(
            (b.submission.date_of_receipt for b in pending if b.submission),
            default=self._clock.now().date(),
        )
        try:
            latest = {
                sub.number: sub for sub in self._gateway.iter_bills(term, received_from=earliest)
            }
        except ServiceUnavailableError as exc:
            result.fatal_error = exc.describe()
            log.error("aborting tracking phase: %s", result.fatal_error)
            return False
        for bill in pending:
            sub = latest.get(bill.number)
            if sub is None:
                continue
            try:
                self._repo.save_submission(term, bill.number, sub)
                if sub.print_number:
                    self._link(bill, sub.print_number, result, publish=publish)
                elif sub.is_closed and not self._repo.closure_announced(term, bill.number):
                    self._announce_withdrawal(bill, result, publish=publish)
            except ServiceUnavailableError as exc:
                result.fatal_error = exc.describe()
                log.error("aborting tracking phase: %s", result.fatal_error)
                return False
            except Exception as exc:
                result.failed += 1
                log.exception("reconciling %s failed: %s", bill.number, exc)
        return True

    def _link(self, pre: Bill, print_number: str, result: TrackingResult, *, publish: bool) -> None:
        """The RPW entry got a print number: continue under the print, in the same thread."""
        now = self._clock.now()
        detail = self._gateway.get_process(pre.term, print_number)
        self._repo.upsert_summary(detail, now=now)
        self._repo.save_stages(
            pre.term, print_number, detail.stages, stage_fingerprint(detail.stages)
        )
        self._repo.set_status(pre.term, print_number, pre.status, prefilter_hits=pre.prefilter_hits)
        if pre.analysis is not None:
            self._repo.save_analysis(pre.term, print_number, pre.analysis)
        if pre.submission is not None:
            self._repo.save_submission(pre.term, print_number, pre.submission)
        self._repo.link_bills(pre.term, pre.number, print_number)
        result.linked += 1
        log.info("%s became druk %s", pre.number, print_number)

        card = self._repo.get_publication(
            pre.term, pre.number, PublicationKind.NEW_BILL.value, self._channel_id
        )
        if card is None or card.status is not PublicationStatus.SENT:
            return  # never posted: the print goes through the normal publishing path
        # The card stays the thread root: the print inherits it instead of getting a second card.
        pub_id = self._repo.create_publication(
            Publication(
                term=pre.term,
                number=print_number,
                kind=PublicationKind.NEW_BILL,
                status=PublicationStatus.SENT,
                channel_id=self._channel_id,
                message_id=card.message_id,
                created_at=now,
            )
        )
        self._repo.mark_publication(pub_id, PublicationStatus.SENT, message_id=card.message_id)

        bill = self._repo.get(pre.term, print_number)
        if bill is None:
            return
        content_changed = False
        if self._analysis is not None and bill.analysis is not None:
            document = self._analysis.newer_document(bill, detail, self._safe_print(bill))
            if document is not None:
                record = self._analysis.reanalyze_bill(bill, detail, document)
                result.reanalyzed += 1
                result.input_tokens += record.input_tokens or 0
                result.output_tokens += record.output_tokens or 0
                add_usage(result.usage, record)
                content_changed = True
        fresh = self._repo.get(pre.term, print_number) or bill
        new_stages = [self._enrich(pre.term, st) for st in diff_stages((), detail.stages)]
        change = StatusChange(
            term=pre.term,
            number=print_number,
            old_fingerprint=pre.number,
            new_fingerprint=self._change_key(stage_fingerprint(detail.stages), fresh, closed=False),
            new_stages=new_stages,
            passed=detail.passed,
            content_changed=content_changed,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return
        change.id = change_id
        result.changed += 1
        if publish:
            self._count(self._publish(fresh, change), result)

    def _announce_withdrawal(self, bill: Bill, result: TrackingResult, *, publish: bool) -> None:
        now = self._clock.now()
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=bill.stages_fingerprint,
            new_fingerprint=hashlib.sha256(f"withdrawn|{bill.number}".encode()).hexdigest(),
            new_stages=[],
            closure_detected=True,
            passed=False,
            withdrawn=True,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return
        change.id = change_id
        result.changed += 1
        log.info("%s withdrawn before getting a print number", bill.number)
        if publish:
            self._count(self._publish(bill, change), result)

    @staticmethod
    def _count(ok: bool, result: TrackingResult) -> None:
        if ok:
            result.published += 1
        else:
            result.failed += 1

    # ------------------------------------------------------------------ detection

    def _fetch(self, bill: Bill) -> tuple[ProcessDetail, PrintInfo | None]:
        """What detection needs from the API for one bill (no database access)."""
        return self._gateway.get_process(bill.term, bill.number), self._safe_print(bill)

    def _detect(
        self,
        bill: Bill,
        detail: ProcessDetail,
        print_info: PrintInfo | None,
        result: TrackingResult,
    ) -> StatusChange | None:
        now = self._clock.now()

        new_fp = stage_fingerprint(detail.stages)
        old_fp = bill.stages_fingerprint
        stages_changed = new_fp != old_fp
        if stages_changed:
            self._repo.save_stages(bill.term, bill.number, detail.stages, new_fp)
        self._repo.upsert_summary(detail, now=now)

        document = (
            self._analysis.newer_document(bill, detail, print_info) if self._analysis else None
        )
        content_changed = False
        if document is not None and self._analysis is not None:
            log.info("druk %s: new text (%s), re-analysing", bill.number, document.kind)
            record = self._analysis.reanalyze_bill(bill, detail, document)
            result.reanalyzed += 1
            result.input_tokens += record.input_tokens or 0
            result.output_tokens += record.output_tokens or 0
            add_usage(result.usage, record)
            content_changed = True

        if old_fp is None:
            return None  # first time we see stages for this bill: seed silently
        # Discovery refreshes the summary (and its closure date) before tracking runs, so closure
        # is detected against what was already announced, not against the stored summary.
        closure_detected = detail.closure_date is not None and not self._repo.closure_announced(
            bill.term, bill.number
        )
        new_stages = diff_stages(bill.stages, detail.stages) if stages_changed else []
        if not (new_stages or content_changed or closure_detected):
            return None  # nothing worth a post (e.g. a stage was edited or removed upstream)
        new_stages = [self._enrich(bill.term, st) for st in new_stages]

        fresh = self._repo.get(bill.term, bill.number) or bill
        change = StatusChange(
            term=bill.term,
            number=bill.number,
            old_fingerprint=old_fp,
            new_fingerprint=self._change_key(new_fp, fresh, closed=closure_detected),
            new_stages=new_stages,
            closure_detected=closure_detected,
            passed=detail.passed,
            content_changed=content_changed,
            detected_at=now,
        )
        change_id = self._repo.add_status_change(change)
        if change_id is None:
            return None  # already recorded by an earlier run
        change.id = change_id
        log.info(
            "druk %s: %d new stage(s)%s: %s",
            bill.number,
            len(change.new_stages),
            " + new text" if content_changed else "",
            "; ".join(s.stage_name for s in change.new_stages) or "-",
        )
        return change

    def _enrich(self, term: int, stage: Stage) -> Stage:
        """Add what the stage tree does not carry: per-club votes and committee names.

        Best effort: a failure here degrades the update, it never blocks it (except an outage).
        """
        try:
            if (
                self._club_breakdown
                and stage.stage_type == "Voting"
                and stage.voting is not None
                and not stage.voting.clubs
                and stage.voting.sitting is not None
                and stage.voting.voting_number is not None
            ):
                votes = self._gateway.get_voting(
                    term, stage.voting.sitting, stage.voting.voting_number
                )
                voting = stage.voting.model_copy(update={"clubs": aggregate_clubs(votes)})
                return stage.model_copy(update={"voting": voting})
            if stage.stage_type == "Referral" and stage.committee_code and not stage.committee_name:
                name = self._committee_name(term, stage.committee_code)
                return stage.model_copy(update={"committee_name": name})
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            log.warning("could not enrich stage %s: %s", stage.stage_name, exc)
        return stage

    def _committee_name(self, term: int, code: str) -> str:
        if code not in self._committee_names:
            self._committee_names[code] = self._gateway.get_committee(term, code).name
        return self._committee_names[code]

    @staticmethod
    def _change_key(stage_fp: str, bill: Bill, *, closed: bool) -> str:
        """Dedupe key for status_changes: stages, analysed text revision, closure announcement."""
        revision = bill.analysis.revision if bill.analysis else 0
        source = bill.analysis.source_url if bill.analysis else ""
        key = f"{stage_fp}|{revision}|{source}" + ("|closed" if closed else "")
        return hashlib.sha256(key.encode()).hexdigest()

    def _safe_print(self, bill: Bill) -> PrintInfo | None:
        try:
            return self._gateway.get_print(bill.term, bill.number)
        except Exception as exc:
            log.warning("print %s unavailable: %s", bill.number, exc)
            return None

    # ------------------------------------------------------------------ published acts

    def _check_act(
        self, bill: Bill, detail: ProcessDetail, result: TrackingResult, *, publish: bool
    ) -> None:
        """Once the process carries an ELI, fetch the act and announce the publication once."""
        if self._eli is None or not detail.eli:
            return
        act = bill.act
        if act is None or (act.entry_into_force is None and detail.eli == act.eli):
            fetched = self._eli.get_act(detail.eli)
            if fetched is None:
                log.info("druk %s: act %s not in the ELI API yet", bill.number, detail.eli)
                return
            if act is not None and fetched.entry_into_force is None:
                return  # nothing new
            act = fetched
            self._repo.save_act(bill.term, bill.number, act)
            log.info(
                "druk %s published as %s, in force %s",
                bill.number,
                act.display_address,
                act.entry_into_force,
            )
        if not publish or self._posted(bill, PublicationKind.ACT_PUBLISHED):
            return
        fresh = self._repo.get(bill.term, bill.number) or bill
        if self._publish_kind(fresh, PublicationKind.ACT_PUBLISHED):
            result.acts_published += 1
        else:
            result.failed += 1

    def _remind_in_force(self, term: int, result: TrackingResult) -> None:
        today = self._clock.now().astimezone(self._local_tz).date()
        for bill in self._repo.list_due_in_force(term, self._channel_id, today=today):
            act = bill.act
            if act is None or act.entry_into_force is None:
                continue
            if act.already_in_force_when_fetched:
                # Discovered late: the publication notice already said "in force since ...".
                self._record(bill, PublicationKind.IN_FORCE, PublicationStatus.SKIPPED)
                continue
            if self._posted(bill, PublicationKind.IN_FORCE):
                continue
            try:
                if self._publish_kind(bill, PublicationKind.IN_FORCE):
                    result.in_force_posted += 1
                else:
                    result.failed += 1
            except ServiceUnavailableError as exc:
                result.failed += 1
                result.fatal_error = exc.describe()
                log.error("aborting tracking phase: %s", result.fatal_error)
                return

    def _posted(self, bill: Bill, kind: PublicationKind) -> bool:
        """True when a post of this kind exists and must not be attempted (again)."""
        pub = self._repo.get_publication(bill.term, bill.number, kind.value, self._channel_id)
        if pub is None:
            return False
        if pub.status is PublicationStatus.FAILED:
            return pub.attempts >= self._max_publish_attempts
        return True

    def _record(self, bill: Bill, kind: PublicationKind, status: PublicationStatus) -> int:
        return self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=kind,
                status=status,
                channel_id=self._channel_id,
                created_at=self._clock.now(),
            )
        )

    def _publish_kind(self, bill: Bill, kind: PublicationKind) -> bool:
        """Post an act notice or an in-force reminder as a reply to the card (pending first)."""
        pub_id = self._record(bill, kind, PublicationStatus.PENDING)
        card = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL.value, self._channel_id
        )
        reply_to = card.message_id if card else None
        try:
            if kind is PublicationKind.ACT_PUBLISHED:
                sent = self._publisher.publish_act_published(bill, reply_to)
            else:
                sent = self._publisher.publish_in_force(bill, reply_to)
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(pub_id, PublicationStatus.FAILED, error=exc.describe())
            raise
        except Exception as exc:
            log.exception("%s post for druk %s failed: %s", kind.value, bill.number, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return False
        self._repo.mark_publication(
            pub_id, PublicationStatus.SENT, message_id=sent.message_id, sent_at=self._clock.now()
        )
        return True

    # ------------------------------------------------------------------ publishing

    def _publish(self, bill: Bill, change: StatusChange) -> bool:
        assert change.id is not None
        now = self._clock.now()
        pub_id = self._repo.create_publication(
            Publication(
                term=bill.term,
                number=bill.number,
                kind=PublicationKind.STATUS_UPDATE,
                status=PublicationStatus.PENDING,
                channel_id=self._channel_id,
                status_change_id=change.id,
                created_at=now,
            )
        )
        original = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL.value, self._channel_id
        )
        reply_to = original.message_id if original else None
        fresh = self._repo.get(bill.term, bill.number) or bill
        try:
            sent = self._publisher.publish_status_update(fresh, change, reply_to)
        except ServiceUnavailableError as exc:
            self._repo.mark_publication(pub_id, PublicationStatus.FAILED, error=exc.describe())
            raise
        except Exception as exc:
            log.exception("status update for druk %s failed: %s", bill.number, exc)
            self._repo.mark_publication(
                pub_id, PublicationStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
            )
            return False
        self._repo.mark_publication(
            pub_id, PublicationStatus.SENT, message_id=sent.message_id, sent_at=self._clock.now()
        )
        return True
