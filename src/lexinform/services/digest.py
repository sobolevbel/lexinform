"""The weekly digest: a draft to the technical channel, published to readers only on approval.

The draft carries a button; pressing it files `/digest publish ref=…` like any operator command,
and the run that executes it builds the week again from the database before it posts. So nothing
reaches readers unread, and nothing a reader sees was assembled more than a moment before.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from zoneinfo import ZoneInfo

from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    Bill,
    DeliveryPlan,
    Digest,
    DigestEntry,
    MonthFigures,
    Phase,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunMode,
    RunReport,
    StatusChange,
    Upcoming,
    WeekFigures,
    adoption_signature,
    consultation_open,
    flatten_stages,
    is_first_digest_of_month,
    iso_week,
    participation_view,
    previous_week,
    update_event,
    week_bounds,
)
from lexinform.ports import BillRepository, Clock, Publisher
from lexinform.services.publications import send_publication

log = logging.getLogger(__name__)

CARD_KINDS = (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL)
UPDATE_KINDS = (
    PublicationKind.STATUS_UPDATE,
    PublicationKind.ACT_PUBLISHED,
    PublicationKind.IN_FORCE,
    PublicationKind.CONSULTATION_RESULTS,
    PublicationKind.CONSULTATION_REVIEW,
    PublicationKind.ACT_APPLICATION,
    PublicationKind.EFFECTIVE_RULES,
)
"""The replies a week is worth telling again. The reminders and the agenda posts are not: each
was about a date, and by Sunday that date has either passed or is in "what is ahead" below."""

SITTINGS_AHEAD_DAYS = 14
SUNDAY = 6
"""The day an ISO week ends on, which is not the same question as the day a digest goes out."""


def _add_or_replace(entries: list[DigestEntry], slots: dict[int, int], entry: DigestEntry) -> None:
    """A `new_bill` row and its later alias share one Telegram `message_id`, so a project with a
    druk the same week its card was sent must not appear twice under two numbers. `message_id` is
    `None` only for a row that was never actually sent, which the digest's own `sent`-only query
    should not produce; treated as never colliding, so such a row is not silently dropped either.
    The later post wins: its bill row is the one still current, the one aliased away is not.
    """
    if entry.message_id is None:
        entries.append(entry)
        return
    slot = slots.get(entry.message_id)
    if slot is None:
        slots[entry.message_id] = len(entries)
        entries.append(entry)
    else:
        entries[slot] = entry


def _wykaz_number(bill: Bill) -> str | None:
    """The number a government row is known by, the same one its card's header uses."""
    if bill.wykaz is not None:
        return bill.wykaz.number
    if bill.rcl is not None:
        return bill.rcl.wykaz_number
    return bill.linked_wykaz_number


@dataclass
class DigestResult:
    """What one digest phase or command did; `ref` is the week it was about."""

    ref: str = ""
    drafted: bool = False
    published: bool = False
    failed: bool = False
    note: str = ""
    message_id: int | None = None
    reader_parts: int = 0
    covered_events: int = 0


class DigestService:
    """Builds the week's digest, drafts it for the operator and publishes it on approval."""

    def __init__(
        self,
        repo: BillRepository,
        publisher: Publisher,
        drafter: Publisher | None,
        clock: Clock,
        *,
        channel_id: str,
        draft_channel_id: str,
        approve_label: str,
        weekday: int = 6,
        local_tz: ZoneInfo = ZoneInfo("Europe/Warsaw"),
        closed_grace_days: int = 90,
        pending_decision_max_days: int = 1095,
        attention_enabled: bool = False,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._drafter = drafter
        self._clock = clock
        self._channel_id = channel_id
        self._draft_channel_id = draft_channel_id
        self._approve = approve_label
        self._weekday = weekday
        self._tz = local_tz
        self._closed_grace_days = closed_grace_days
        self._pending_decision_max_days = pending_decision_max_days
        self._attention_enabled = attention_enabled

    def today(self) -> dt.date:
        """The reader's day, which is the Sejm's: a digest is due on a Warsaw Sunday."""
        return self._clock.now().astimezone(self._tz).date()

    def owns_channel(self, channel: str) -> bool:
        return channel in (self._channel_id, self._draft_channel_id)

    def current_ref(self) -> str:
        """The last ISO week that has ended, which is what a digest sent now is about.

        Asked of the calendar and not of `digest_weekday`: an ISO week ends on a Sunday whatever
        day the digest goes out on, so a Monday digest was drafting the week that had just begun.
        """
        today = self.today()
        return iso_week(today) if today.weekday() == SUNDAY else previous_week(today)

    def run(self, *, current_report: RunReport | None = None) -> DigestResult:
        """The digest phase of a run: draft the week on its day, and nothing on any other."""
        resumed = self._resume_parts()
        if resumed is not None:
            return resumed
        if self._drafter is None:
            return DigestResult(note="no technical channel: a digest is never sent unread")
        if self.today().weekday() != self._weekday:
            return DigestResult(note="not the digest's day")
        return self.draft(self.current_ref(), current_report=current_report, repeat=False)

    def draft(
        self, ref: str, *, current_report: RunReport | None = None, repeat: bool = True
    ) -> DigestResult:
        """Post the week's digest to the technical channel with the button that publishes it."""
        drafter = self._drafter
        if drafter is None:
            return DigestResult(ref=ref, note="no technical channel to draft into")
        existing = self._repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, self._draft_channel_id, ref=ref
        )
        if (
            not repeat
            and existing is not None
            and existing.status is PublicationStatus.SENT
            and self._parts_complete(existing)
        ):
            return DigestResult(ref=ref, message_id=existing.message_id, note="drafted already")
        delivery_ref = ref
        while (
            existing is not None
            and existing.status is PublicationStatus.SENT
            and self._parts_complete(existing)
        ):
            delivery_ref = f"{ref}:draft:{existing.id}"
            existing = self._repo.get_publication(
                DIGEST_TERM,
                DIGEST_NUMBER,
                PublicationKind.DIGEST,
                self._draft_channel_id,
                ref=delivery_ref,
            )
        week = self.build(ref, current_report=current_report)
        message_id = self._send(
            self._draft_channel_id,
            week,
            lambda: drafter.publish_digest(week, approve=self._approve).message_id,
            delivery_ref=delivery_ref,
            publisher=drafter,
        )
        if message_id is None:
            return DigestResult(ref=ref, failed=True, note=f"{ref} was not posted, see the log")
        return DigestResult(ref=ref, drafted=True, message_id=message_id)

    def publish(self, ref: str) -> DigestResult:
        """Post the week's digest to the readers' channel, rebuilt from the database first."""
        existing = self._repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, self._channel_id, ref=ref
        )
        if (
            existing is not None
            and existing.status is PublicationStatus.SENT
            and self._parts_complete(existing)
        ):
            return DigestResult(
                ref=ref, message_id=existing.message_id, note="this week is in the channel already"
            )
        before_parts, before_events = self._delivery_totals(existing)
        week = self.build(ref)
        message_id = self._send(
            self._channel_id,
            week,
            lambda: self._publisher.publish_digest(week).message_id,
            publisher=self._publisher,
        )
        saved = self._repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, self._channel_id, ref=ref
        )
        after_parts, after_events = self._delivery_totals(saved)
        reader_parts = max(0, after_parts - before_parts)
        covered_events = max(0, after_events - before_events)
        if message_id is None:
            return DigestResult(
                ref=ref,
                failed=True,
                note=f"{ref} was not posted, see the log",
                reader_parts=reader_parts,
                covered_events=covered_events,
            )
        return DigestResult(
            ref=ref,
            published=True,
            message_id=message_id,
            reader_parts=reader_parts,
            covered_events=covered_events,
        )

    def build(self, ref: str, *, current_report: RunReport | None = None) -> Digest:
        """The week as the channel lived it, plus what a reader can still act on."""
        since, until = week_bounds(ref)
        posts = self._repo.list_publications_between(
            self._channel_id,
            since=self._midnight(since),
            until=self._midnight(until + dt.timedelta(days=1)),
        )
        posts = [
            post for post in posts if post.id is None or self._repo.digest_owner(post.id) is None
        ]
        posts.extend(
            self._repo.list_digest_candidates(
                self._channel_id,
                eligible_before=min(
                    self._clock.now() + dt.timedelta(milliseconds=1),
                    self._midnight(until + dt.timedelta(days=1)),
                ),
            )
        )
        bills: dict[tuple[int, str], Bill] = {}
        cards: list[DigestEntry] = []
        updates: list[DigestEntry] = []
        card_slots: dict[int, int] = {}  # message_id -> index in `cards`
        update_slots: dict[tuple[int | None, int, str], int] = {}
        grouped_messages = {
            post.message_id
            for post in posts
            if (post.ref is not None and post.ref.startswith("updates/"))
            or (post.delivery is not None and post.delivery.update_group)
        }
        for post in posts:
            if post.delivery is not None and post.delivery.absorbed_by is not None:
                continue
            if post.delivery is not None and post.delivery.edit_message_id is not None:
                continue
            entry = self._entry(post, bills)
            if entry is None:
                continue
            if post.kind in CARD_KINDS:
                _add_or_replace(cards, card_slots, entry)
            elif post.kind in UPDATE_KINDS or (
                post.delivery is not None and post.delivery.destination == "digest_only"
            ):
                key = (
                    (None, entry.term, f"{entry.number}/{post.id}")
                    if entry.message_id is None
                    else (
                        (entry.message_id, entry.term, entry.number)
                        if entry.message_id in grouped_messages
                        else (entry.message_id, 0, "")
                    )
                )
                slot = update_slots.get(key)
                if slot is None:
                    update_slots[key] = len(updates)
                    updates.append(entry)
                else:
                    updates[slot] = entry
        merged: list[DigestEntry] = []
        decisions: dict[tuple[str, str], int] = {}
        for entry in updates:
            decision_key = (entry.case_key, entry.decision_key)
            if entry.decision_key and decision_key in decisions:
                index = decisions[decision_key]
                earlier = merged[index]
                votes = {
                    (vote.sitting, vote.voting_number): vote
                    for vote in (*earlier.votings, *entry.votings)
                }
                merged[index] = earlier.model_copy(
                    update={
                        "source_publication_ids": tuple(
                            dict.fromkeys(
                                (*earlier.source_publication_ids, *entry.source_publication_ids)
                            )
                        ),
                        "votings": tuple(votes.values()),
                        "consequence": earlier.consequence or entry.consequence,
                        "event": "passed",
                    }
                )
            else:
                if entry.decision_key:
                    decisions[decision_key] = len(merged)
                merged.append(entry)
        updates = merged
        consultations, sittings = self._ahead()
        starts = self._midnight(since)
        ends = self._midnight(until + dt.timedelta(days=1))
        reports = self._repo.list_runs(since=starts)
        if current_report is not None:
            reports.append(current_report)
        reports = [
            r for r in reports if starts <= r.started_at < ends and r.mode is not RunMode.DRY_RUN
        ]
        return Digest(
            ref=ref,
            pending_events=sum(
                pub.kind is not PublicationKind.DIGEST
                for pub in self._repo.list_open_deliveries(self._channel_id)
            ),
            unknown_deliveries=sum(
                pub.status is PublicationStatus.UNKNOWN
                for pub in self._repo.list_open_deliveries(self._channel_id)
            ),
            checks_incomplete=any(report.errors for report in reports) or not reports,
            since=since,
            until=until,
            # Everything the week held: how much of it fits one message is the renderer's
            # business, and only the renderer can say how many it left out.
            cards=tuple(cards),
            updates=tuple(updates),
            consultations=tuple(consultations),
            sittings=tuple(sittings),
            month=self._month(ref),
            figures=WeekFigures.of(reports) if reports else None,
            highlights=tuple(
                sorted(
                    [
                        entry
                        for entry in [*cards, *updates]
                        if entry.attention is not None and entry.attention.importance == "important"
                    ],
                    key=lambda entry: (
                        entry.attention.urgency != "urgent" if entry.attention else True,
                        entry.attention.deadline or dt.date.max if entry.attention else dt.date.max,
                        entry.term,
                        entry.number,
                        entry.sent_at,
                    ),
                )[:3]
            ),
        )

    def _entry(self, post: Publication, bills: dict[tuple[int, str], Bill]) -> DigestEntry | None:
        """One post as the digest names it; None when its bill is gone from the database."""
        key = (post.term, post.number)
        if key not in bills:
            found = self._repo.get(post.term, post.number)
            if found is None:
                return None
            bills[key] = found
        bill = bills[key]
        if post.delivery is not None and post.delivery.bill_json:
            bill = participation_view(
                Bill.model_validate_json(post.delivery.bill_json), post.delivery.participation
            )
        historical_snapshot = post.delivery is not None and bool(post.delivery.bill_json)
        analysis = (
            bill.analysis.analysis if bill.analysis is not None and historical_snapshot else None
        )
        change = None
        if post.delivery is not None and post.delivery.change_json:
            change = StatusChange.model_validate_json(post.delivery.change_json)
        consequence = ""
        if (
            post.kind is PublicationKind.CONSULTATION_REVIEW
            and bill.consultation_review is not None
            and historical_snapshot
        ):
            consequence = " ".join(
                finding.text_change or finding.reason
                for finding in bill.consultation_review.review.consultation_findings
                if finding.outcome != "unknown"
            )
        elif (
            post.kind in (PublicationKind.ACT_APPLICATION, PublicationKind.EFFECTIVE_RULES)
            and bill.act_application_review is not None
            and historical_snapshot
        ):
            consequence = " ".join(
                rule.change for rule in bill.act_application_review.review.effective_rules
            )
        if analysis is not None:
            if change is not None and change.content_changed:
                consequence = " ".join(analysis.changes_since_previous[:2])
            elif post.kind in CARD_KINDS:
                consequence = " ".join(analysis.key_changes[:2])
        return DigestEntry(
            term=bill.term,
            number=bill.number,
            title=bill.summary.title,
            mnemonic=bill.editorial.mnemonic.name if bill.editorial.mnemonic else "",
            mnemonic_language=bill.editorial.mnemonic.language if bill.editorial.mnemonic else "ru",
            consequence=consequence,
            organ_deadline=Phase.model_validate_json(post.delivery.phase_json).deadline
            if post.delivery is not None and post.delivery.phase_json
            else None,
            case_key=f"thread/{post.delivery.reply_to}"
            if post.delivery is not None and post.delivery.reply_to is not None
            else f"{bill.term}/{bill.number}",
            decision_key=adoption_signature(change, bill) if change is not None else "",
            votings=tuple(
                stage.voting
                for stage in flatten_stages(change.new_stages)
                if stage.voting is not None
            )
            if change is not None
            else (),
            attention=post.attention,
            historical_snapshot=post.delivery is not None and bool(post.delivery.bill_json),
            summary=analysis.summary if analysis is not None and post.delivery is not None else "",
            source_publication_ids=(post.id,)
            if post.id is not None
            and post.delivery is not None
            and post.delivery.destination == "digest_only"
            and post.status is PublicationStatus.QUEUED
            else (),
            kind=post.kind,
            sent_at=post.sent_at or post.created_at,
            message_id=post.message_id,
            score=analysis.score if analysis is not None else None,
            category=analysis.category if analysis is not None else None,
            wykaz_number=_wykaz_number(bill),
            event=update_event(change, bill)
            if change is not None and post.delivery is not None
            else "",
        )

    def _event(self, post: Publication, bill: Bill) -> str:
        """What an update said, read from the change it was written for and not from the bill's
        state today, which has moved on since."""
        if post.kind is not PublicationKind.STATUS_UPDATE or post.status_change_id is None:
            return ""
        change = self._repo.get_status_change(post.status_change_id)
        return update_event(change, bill) if change is not None else ""

    def _ahead(self) -> tuple[list[Upcoming], list[Upcoming]]:
        """What the followed bills still offer: an open consultation, a sitting in a fortnight."""
        today = self.today()
        horizon = today + dt.timedelta(days=SITTINGS_AHEAD_DAYS)
        consultations: list[Upcoming] = []
        sittings: list[Upcoming] = []
        for bill in self._followed():
            window = bill.consultation
            if window is not None and window.end is not None and consultation_open(bill, today):
                consultations.append(self._upcoming(bill, deadline=window.end))
            for item in sorted(bill.agenda, key=lambda i: (i.date, i.start_time or dt.time())):
                if today <= item.date <= horizon:
                    sittings.append(self._upcoming(bill, sitting=item))
        consultations.sort(key=lambda w: (w.deadline or horizon, w.number))
        sittings.sort(key=lambda w: (w.sitting.date if w.sitting else horizon, w.number))
        return consultations, sittings

    def _followed(self) -> list[Bill]:
        return self._repo.list_tracked(
            self._channel_id,
            closed_grace_days=self._closed_grace_days,
            pending_decision_max_days=self._pending_decision_max_days,
            now=self._clock.now(),
        )

    def _upcoming(self, bill: Bill, **what: object) -> Upcoming:
        card = self._repo.get_publication(
            bill.term, bill.number, PublicationKind.NEW_BILL, self._channel_id
        )
        return Upcoming(
            term=bill.term,
            number=bill.number,
            title=bill.summary.title,
            mnemonic=bill.editorial.mnemonic.name if bill.editorial.mnemonic else "",
            mnemonic_language=bill.editorial.mnemonic.language if bill.editorial.mnemonic else "ru",
            action_url=bill.consultation.survey_url or bill.consultation.form_url
            if bill.consultation
            else None,
            action_email=bill.consultation.email if bill.consultation else None,
            source_url=bill.consultation.letter_url
            or bill.consultation.form_url
            or bill.consultation.survey_url
            if bill.consultation
            else None,
            affected_groups=tuple(bill.analysis.analysis.affected_groups) if bill.analysis else (),
            summary=bill.analysis.analysis.summary if bill.analysis else "",
            message_id=card.message_id if card is not None else None,
            wykaz_number=_wykaz_number(bill),
            **what,
        )

    def _month(self, ref: str) -> MonthFigures | None:
        """The figures of the month just ended, in the first digest of a month and in no other.

        The month reported is the one before the digest's own, so that it is complete; run
        records are kept for ninety days, which always covers it."""
        if not is_first_digest_of_month(ref):
            return None
        _, sunday = week_bounds(ref)
        this_month = sunday.replace(day=1)
        last_month = (this_month - dt.timedelta(days=1)).replace(day=1)
        ends = self._midnight(this_month)
        reports = [
            report
            for report in self._repo.list_runs(since=self._midnight(last_month))
            if report.started_at.astimezone(dt.UTC) < ends
        ]
        return MonthFigures.of(last_month, reports)

    def _midnight(self, day: dt.date) -> dt.datetime:
        """The start of a Warsaw day, in the UTC the `sent_at` column holds."""
        return dt.datetime.combine(day, dt.time(), tzinfo=self._tz).astimezone(dt.UTC)

    def _send(
        self,
        channel_id: str,
        week: Digest,
        post: Callable[[], int],
        *,
        delivery_ref: str | None = None,
        publisher: Publisher | None = None,
    ) -> int | None:
        """Record the row before sending it, and the outcome after; the message id on success."""
        ref = delivery_ref or week.ref
        existing = self._repo.get_publication(
            DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, channel_id, ref=ref
        )
        if (
            existing is not None
            and existing.delivery is not None
            and existing.delivery.digest_parts
        ) or (self._attention_enabled and existing is None):
            assert publisher is not None
            return self._send_parts(channel_id, week, publisher, ref=ref, existing=existing)
        if existing is not None and existing.status in (
            PublicationStatus.PENDING,
            PublicationStatus.UNKNOWN,
            PublicationStatus.DISMISSED,
        ):
            log.warning(
                "digest %s not resent: publication %s is %s",
                week.ref,
                existing.id,
                existing.status,
            )
            return None
        pub_id = self._repo.create_publication(
            Publication(
                term=DIGEST_TERM,
                number=DIGEST_NUMBER,
                kind=PublicationKind.DIGEST,
                status=PublicationStatus.PENDING,
                channel_id=channel_id,
                ref=ref,
                created_at=self._clock.now(),
            )
        )
        return send_publication(self._repo, self._clock, pub_id, post)

    def _delivery_totals(self, root: Publication | None) -> tuple[int, int]:
        if root is None or root.id is None:
            return 0, 0
        identities = (
            root.delivery.digest_parts
            if root.delivery and root.delivery.digest_parts
            else (root.id,)
        )
        parts = [self._repo.publication_by_id(identity) for identity in identities]
        return (
            sum(part is not None and part.status is PublicationStatus.SENT for part in parts),
            sum(
                source.status is PublicationStatus.SENT
                for identity in identities
                for source in self._repo.digest_sources(identity)
            ),
        )

    def _parts_complete(self, publication: Publication) -> bool:
        if publication.delivery is None or not publication.delivery.digest_parts:
            return True
        return all(
            part is not None and part.status is PublicationStatus.SENT
            for identity in publication.delivery.digest_parts
            for part in (self._repo.publication_by_id(identity),)
        )

    def _send_parts(
        self,
        channel_id: str,
        week: Digest,
        publisher: Publisher,
        *,
        ref: str,
        existing: Publication | None,
    ) -> int | None:
        if existing is None:
            parts = publisher.render_digest_parts(week, draft=channel_id == self._draft_channel_id)
            identities: list[int] = []
            with self._repo.atomic():
                for index, rendered_part in enumerate(parts):
                    identities.append(
                        self._repo.create_publication(
                            Publication(
                                term=DIGEST_TERM,
                                number=DIGEST_NUMBER,
                                kind=PublicationKind.DIGEST,
                                status=PublicationStatus.QUEUED,
                                channel_id=channel_id,
                                ref=ref if index == 0 else f"{ref}:part:{index + 1}",
                                created_at=self._clock.now(),
                                delivery=DeliveryPlan(
                                    bill_json="",
                                    rendered_text=rendered_part.text,
                                    digest_event_ids=rendered_part.event_ids,
                                    policy_version="attention-v1",
                                    digest_ref=week.ref,
                                    approve_label=self._approve
                                    if channel_id == self._draft_channel_id
                                    and index == len(parts) - 1
                                    else "",
                                ),
                            )
                        )
                    )
                    if rendered_part.event_ids:
                        self._repo.reserve_digest_events(identities[-1], rendered_part.event_ids)
                root = self._repo.publication_by_id(identities[0])
                assert root is not None and root.delivery is not None and root.id is not None
                assert self._repo.replace_unattempted_delivery(
                    root.id, root.delivery.model_copy(update={"digest_parts": tuple(identities)})
                )
            existing = self._repo.publication_by_id(identities[0])
        assert existing is not None and existing.delivery is not None
        for identity in existing.delivery.digest_parts:
            part = self._repo.publication_by_id(identity)
            assert part is not None
            if part.status is PublicationStatus.SENT:
                continue
            if part.status not in (PublicationStatus.QUEUED, PublicationStatus.FAILED):
                return None
            self._repo.mark_publication(identity, PublicationStatus.PENDING, count_attempt=False)
            if (
                send_publication(
                    self._repo, self._clock, identity, partial(publisher.publish_prepared, part)
                )
                is None
            ):
                return None
        root = self._repo.publication_by_id(existing.id) if existing.id is not None else None
        return root.message_id if root is not None else None

    def _resume_parts(self) -> DigestResult | None:
        for channel, publisher in (
            (self._channel_id, self._publisher),
            (self._draft_channel_id, self._drafter),
        ):
            if publisher is None:
                continue
            for part in self._repo.list_due_deliveries(channel, max_attempts=3):
                if part.kind is not PublicationKind.DIGEST or part.delivery is None or not part.ref:
                    continue
                root_ref = part.ref.split(":part:", 1)[0]
                root = self._repo.get_publication(
                    DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, channel, ref=root_ref
                )
                if root is None or root.delivery is None or not root.delivery.digest_parts:
                    continue
                members = [
                    self._repo.publication_by_id(identity)
                    for identity in root.delivery.digest_parts
                ]
                if any(
                    member is None
                    or member.status
                    not in (
                        PublicationStatus.QUEUED,
                        PublicationStatus.FAILED,
                        PublicationStatus.SENT,
                    )
                    for member in members
                ):
                    continue
                ref = part.delivery.digest_ref
                before_parts, before_events = self._delivery_totals(root)
                sent = self._send_parts(
                    channel, self.build(ref), publisher, ref=root_ref, existing=root
                )
                after_parts, after_events = self._delivery_totals(root)
                return DigestResult(
                    ref=ref,
                    message_id=sent,
                    failed=sent is None,
                    drafted=sent is not None and channel == self._draft_channel_id,
                    published=sent is not None and channel == self._channel_id,
                    reader_parts=max(0, after_parts - before_parts)
                    if channel == self._channel_id
                    else 0,
                    covered_events=max(0, after_events - before_events)
                    if channel == self._channel_id
                    else 0,
                )
        return None
