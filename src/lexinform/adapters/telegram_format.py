"""Renders Telegram HTML messages from domain models. Pure functions, no I/O.

Telegram limit: 4096 characters per message. Everything derived from external data is passed
through html.escape; only our own markup is raw HTML.
"""

import datetime as dt
import html
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from lexinform.i18n import Labels, labels_for
from lexinform.models import (
    COMMITTEE_PHASES,
    DAYS_PER_MONTH,
    GOVERNMENT_STEPS,
    HEARING_APPLICATION_EMAIL,
    PATH_STEPS,
    PHASE_STEP,
    RCL_PREFIX,
    RCL_STAGE_TYPE,
    SENATE_BILLS_URL,
    SITTING_PHASES,
    WYKAZ_REGISTER_URL,
    ActInfo,
    AgendaItem,
    AnalysisVerdict,
    ApplicantType,
    BackfillReport,
    Bill,
    BillStatus,
    CommandOutcome,
    ConsultationWindow,
    Digest,
    DigestEntry,
    IncomingCommand,
    MonthFigures,
    OutcomeStatus,
    Phase,
    PrintInfo,
    Publication,
    PublicationKind,
    RclProject,
    RunReport,
    SpendSnapshot,
    Stage,
    StatusChange,
    StatusSnapshot,
    TokenUsage,
    Upcoming,
    VotingSummary,
    WykazEntry,
    about_ukraine,
    committee_letter_url,
    committee_web_url,
    consultation_open,
    deadline_overdue,
    end_names_veto_sustained,
    event_keys,
    flatten_stages,
    government_path,
    hearing_application_deadline,
    hearing_rules_url,
    is_over,
    is_pre_print_number,
    is_rcl_number,
    is_urgent,
    is_wykaz_number,
    merge_usage,
    ministry_contact_url,
    ministry_name,
    ministry_url,
    next_phase,
    open_hearing,
    process_web_url,
    reaches_sejm,
    stalled_days,
    told_stages,
    update_event,
    usage_of,
    veto_outcome,
    veto_stood,
    wykaz_entry_number,
)
from lexinform.pricing import cost_usd, format_tokens, format_usd

MESSAGE_LIMIT = 4096
ELLIPSIS = "…"
QUOTED_LINE_CHARS = 300  # Telegram takes 4096 characters in a command, and echoes them back
QUARTERS = {1: "I", 2: "II", 3: "III", 4: "IV"}

# What the technical channel accepts (English, like the run report; the operator's language).
COMMAND_HELP = (
    "<b>one bill</b> (a druk number, RPW/…, RCL/…, UC164, RM-… or a link to sejm.gov.pl /"
    " api.sejm.gov.pl / legislacja.rcl.gov.pl):\n"
    "• <code>/analyze BILL [force] [publish] [json]</code> — fetch, prefilter, analyse; post the"
    " card when relevant and important enough, then follow it. <code>force</code> gets past the"
    " prefilter, a previous analysis and the cost guard, <code>publish</code> past the score"
    " threshold, <code>json</code> adds the raw verdict\n"
    "• <code>/show BILL</code> — what the database knows\n"
    "• <code>/preview BILL [to=CHAT]</code> — the card as the channel would get it, rendered"
    " here only, or sent to a test chat\n"
    "• <code>/refresh BILL</code> — check this bill now: stages, act, sittings, its card\n"
    "• <code>/skip BILL</code> — silence a false positive (no analysis, no card)\n"
    "• <code>/unskip BILL</code> — restore saved analysis, or queue if none exists\n"
    "• <code>/reset BILL [to=STATUS]</code> — any status with a clean budget of attempts"
    " (default <code>analysis_pending</code>)\n"
    "• <code>/republish BILL</code> — post the card again\n"
    "• <code>/forget BILL</code> — drop the card the channel remembers, post nothing"
    " (a card deleted by hand)\n"
    "\n<b>the database</b>, answered here and now:\n"
    "• <code>/find WORDS</code> — bills whose title or number contains the words\n"
    "• <code>/status</code> — the queues, what is stuck, what the last runs cost\n"
    "• <code>/delivery ID [confirm MESSAGE_ID | retry | dismiss REASON]</code> — inspect or"
    " resolve an unknown delivery after checking the channel\n"
    "• <code>/runs [days=N] [all]</code> — non-empty runs; all includes empty runs\n"
    "• <code>/cost [days=N] [top=N]</code> — the model spend, per model, run and bill\n"
    "• <code>/digest [ref=2026-W38] [publish]</code> — draft the week's digest here, or send"
    " it to the channel; the draft's button is the same command\n"
    "\n<b>a run</b> — these start the workflow instead of waiting for it:\n"
    "• <code>/run [since=DATE] [dry] [reprefilter=N] [text_skipped] [index_rcl_since=DATE]"
    " [no_publish] [no_track] [no_rcl] [full_track] [max_publish=N] [max_analyze=N]"
    " [min_score=N]</code>\n"
    "• <code>/scan [since=DATE]</code> — discover and keyword-filter only: no model, no posts\n"
    "• <code>/track [dry]</code> — the tracking phase over every followed bill\n"
    "• <code>/collect-batches [dry]</code> — write down a finished batch, then analyse/publish"
    "/track what that frees\n"
    "• <code>/reprefilter [limit=N] [text_skipped]</code> — scan the texts of skipped bills\n"
    "• <code>/index-rcl-numbers since=DATE</code> — read the RCL listing for the wykaz join\n"
    "\n• <code>/help</code>. <code>listen</code>, <code>commands</code>, <code>db</code> and"
    " <code>poll-batches</code> are not commands: they are this relay, this phase, the state"
    " branch around every run and the mikrus timer that asks for a collect run early. Nor are"
    " <code>batch-intents</code>, <code>attach-batch-intents</code> and"
    " <code>recover-batch-intent</code>: they repair the state database after checking the"
    " provider by hand."
)

# The commands whose answer is a dossier and not a verdict: they get the status, the last stage
# and the consultation window as well as the bill's title and score.
FULL_FACTS = frozenset({OutcomeStatus.SHOWN, OutcomeStatus.REFRESHED})

DIGEST_TITLE_CHARS = 90
DIGEST_MAX_PER_SECTION = 10
# The button under the draft, in the operator's language like everything else in that channel.
APPROVE_DIGEST = "📣 Publish to the channel"
# English, like every other line the technical channel gets; the digest under it is the reader's.
DIGEST_DRAFT_NOTE = (
    "<b>📝 draft</b> — press the button to publish it to the channel, or leave it and nothing"
    " goes out. It is rebuilt from the database when it is published, so it cannot go stale here."
)

FILLED = "●"
EMPTY = "○"

SCORE_ICON = {5: "🔴", 4: "🟠", 3: "🟡", 2: "🟢", 1: "⚪"}
ICON = {
    "new_bill": "📜",
    "update": "🔄",
    "importance": "📊",
    "category": "🏷",
    "about": "📝",
    "key_changes": "🔑",
    "practical": "💡",
    "affected": "👥",
    "effective": "📅",
    "stage": "🏛",
    "applicant": "✍️",
    "doc_date": "📄",
    "filed": "📄",
    "links": "🔗",
    "new_stages": "🧭",
    "path": "🗺",
    "changed": "🆕",
    "passed": "✅",
    "closed": "🏁",
    "note": "ℹ️",
    "joint": "🔀",
    "joint_diff": "↔️",
    "voting": "🗳",
    "committee": "📮",
    "consultation": "🗣",
    "print": "🔢",
    "search": "🔎",
    "published": "📖",
    "journal": "📰",
    "in_force": "⚖️",
    "next": "⏭",
    "action": "👉",
    "calendar": "🗓",
    "agenda": "📝",
    "hearing": "📢",
    "wykaz": "⏳",
    "deadline": "⏳",
    "digest": "🗞",
    "month": "📊",
    "support": "☕️",
}
# The header icon of a status update, by event (see `models.update_event`); 🔄 otherwise.
EVENT_ICON = {
    "print_assigned": "🔢",
    "referral": "📮",
    "referrals": "📮",
    "referral_plenary": "🏛",
    "committee_report": "📋",
    "subcommittee_report": "📋",
    "committee_rejects": "❌",
    "hearing": "📢",
    "second_reading_amendments": "📋",
    "third_reading": "🗳",
    "passed": "✅",
    "rejected": "❌",
    "withdrawn_by_applicant": "🏁",
    "veto_sustained": "⛔",
    "veto_overridden": "✅",
    "not_enacted": "🏁",
    "senate": "🏛",
    "senate_no_amendments": "✅",
    "senate_amendments": "📋",
    "senate_rejected": "❌",
    "senate_considered": "🏛",
    "to_president": "🏛",
    "signed": "✍️",
    "veto": "⛔",
    "tribunal": "⚖️",
    "tribunal_ruled": "⚖️",
    "voting": "🗳",
    "text_changed": "🆕",
    "government_position": "🏛",
    "impact_assessment": "📊",
    "withdrawn": "🏁",
    "abandoned": "🏁",
    "discontinued": "🏁",
    "rcl_to_sejm": "🔢",
    "rcl_closed": "🏁",
    "wykaz_adopted": "✅",
    "rcl_started": "📄",
    "consultation_opened": "🗣",
    "wykaz_withdrawn": "🚫",
}
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_READING_NUMERAL = re.compile(r"^\s*(I{1,3})\s+czytanie", re.IGNORECASE)
CLUBS_PER_SIDE = 4
# Events whose header already tells how the process ended: a second sentence would repeat it.
_SELF_EXPLAINING_CLOSURES = frozenset(
    {
        "passed",
        "veto_overridden",
        "rejected",
        "rcl_closed",
        "wykaz_withdrawn",
        "withdrawn_by_applicant",
        "not_enacted",
    }
)


@dataclass(frozen=True)
class RenderedMessage:
    text: str


def importance_bar(score: int) -> str:
    score = max(1, min(5, score))
    return FILLED * score + EMPTY * (5 - score)


def score_icon(score: int) -> str:
    return SCORE_ICON.get(max(1, min(5, score)), "⚪")


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(text)}</a>'


def _supports_key(supports: bool | None) -> str:
    """The government's verdict as a label key; "" when the position states none, which is what
    a conditional one does — the condition is in the summary and a bare "backs it" would lie."""
    if supports is None:
        return ""
    return "yes" if supports else "no"


def lead(text: str) -> str:
    """The first sentence: what an update repeats of the summary the card already carries."""
    first = _SENTENCE_END.split(text.strip(), maxsplit=1)[0]
    return first.strip()


def length(text: str) -> int:
    """What Telegram counts: UTF-16 code units, so every emoji and every rare sign counts twice."""
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


def fit_lines(text: str, limit: int) -> str:
    """Trim to `limit` (as Telegram counts) by dropping whole lines from the end.

    For a body that carries markup: every line of a message is HTML-balanced on its own (the
    invariant `_cut_lines` rests on too), so a cut between lines cannot split a tag, while
    `fit`'s cut at the nearest space can — and Telegram answers 400 to a dangling `<a`.
    """
    if length(text) <= limit:
        return text
    lines = text.split("\n")
    while lines:
        lines.pop()
        kept = "\n".join(lines).rstrip()
        if kept and length(kept) + length(ELLIPSIS) <= limit:
            return kept + ELLIPSIS
    return ""


def fit(text: str, limit: int) -> str:
    """Trim to `limit` (as Telegram counts) at a paragraph/line boundary, with an ellipsis.

    Only for escaped text: it cuts at a space when no line boundary is near enough, which would
    split a tag. Use `fit_lines` where the text carries markup.
    """
    if length(text) <= limit:
        return text
    cut = text[: limit - 1]
    while length(cut) > limit - 1:
        cut = cut[: -max(1, (length(cut) - limit + 1) // 2)]
    boundary = max(cut.rfind("\n\n"), cut.rfind("\n"), cut.rfind(" "))
    if boundary > limit // 2:
        cut = cut[:boundary]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]  # never leave half an HTML entity behind
    return cut.rstrip() + ELLIPSIS


class MessageFormatter:
    """Renders every message kind in one output language; see `i18n.Labels`."""

    def __init__(
        self,
        language: str = "ru",
        *,
        today: Callable[[], dt.date] = dt.date.today,
        channel: str = "",
        support_url: str = "",
        sponsor_url: str = "",
    ) -> None:
        """`today` dates "what comes next", the countdowns and the consultation tag when a
        message method is not given the day explicitly: production passes the run's clock in
        Warsaw time, so a post never depends on the machine's zone.

        `channel` is the reader's channel as the settings spell it, and only an `@name` lets the
        digest link to the posts it lists; the two addresses are the digest's own ask."""
        self._labels: Labels = labels_for(language)
        self._today = today
        self._channel = channel
        self._support_url = support_url
        self._sponsor_url = sponsor_url

    def new_bill(
        self, bill: Bill, print_info: PrintInfo | None, *, today: dt.date | None = None
    ) -> RenderedMessage:
        """The card. `today` decides whether the public consultation still counts as open."""
        if bill.analysis is None:
            raise ValueError(f"bill {bill.number} has no analysis")
        a = bill.analysis.analysis
        lb = self._labels
        today = today or self._today()

        header = self._header(ICON["new_bill"], self._card_header(bill, today), bill)
        meta = (
            f"{score_icon(a.score)} <b>{esc(lb.importance)}:</b> {importance_bar(a.score)} "
            f"{a.score}/5\n"
            + self._field(
                ICON["category"],
                lb.category,
                esc(lb.category_labels.get(a.category, a.category)),
            )
        )
        summary_block = f"{ICON['about']} <b>{esc(lb.about)}</b>\n{esc(a.summary.strip())}"
        changes_block = ""
        if a.key_changes:
            bullets = "\n".join(f"• {esc(c.strip())}" for c in a.key_changes if c.strip())
            changes_block = f"{ICON['key_changes']} <b>{esc(lb.key_changes)}</b>\n{bullets}"

        text = self._assemble(
            [header, meta, self._intention_note(bill)],
            flexible=[summary_block, changes_block],
            tail=[
                self._card_details(bill, today),
                self._links(self._card_links(bill, print_info)),
                self._card_tags(bill, today),
            ],
        )
        return RenderedMessage(text=text)

    def _card_header(self, bill: Bill, today: dt.date) -> str:
        lb = self._labels
        if self._ended_line(bill, today):
            # Only when the card can also say *how* it ended: `next_phase` gives up on a stage
            # tree it does not recognise too, and "процесс завершён" over nothing is a guess.
            return lb.finished_bill_header
        if bill.wykaz is not None:
            return lb.wykaz_header
        return lb.rcl_header if bill.rcl is not None else lb.new_bill_header

    def _intention_note(self, bill: Bill) -> str:
        """The card of a planned bill says so above everything the model wrote: there is no text
        yet, and a reader must not take the analysis of an intention for one of a bill."""
        entry = bill.wykaz
        if entry is None:
            return ""
        note = self._labels.wykaz_intention.format(date=self.fmt_date(entry.published_at.date()))
        return f"{ICON['wykaz']} <i>{esc(note)}</i>"

    def _card_details(self, bill: Bill, today: dt.date) -> str:
        """The card's second half: the analysis' practical facts, the consultation, what comes
        next, then the compact one-line facts (stage, applicant, joint prints, notes)."""
        assert bill.analysis is not None, "a card is rendered only for an analysed bill"
        a = bill.analysis.analysis
        lb = self._labels
        s = bill.summary
        details: list[str] = []
        if a.practical_impact.strip():
            details.append(
                self._field(ICON["practical"], lb.practical_impact, esc(a.practical_impact.strip()))
            )
        if a.affected_groups:
            groups = ", ".join(g.strip() for g in a.affected_groups)
            details.append(self._field(ICON["affected"], lb.affected, esc(groups)))
        # Not on a plan's card: two blocks above it says there is no text yet, and a vacatio
        # legis can only be quoted from one — "не указано" there reads as a fact about the bill.
        if bill.wykaz is None:
            effective = a.effective_date.strip() if a.effective_date else lb.effective_date_unknown
            details.append(self._field(ICON["effective"], lb.effective_date, esc(effective)))
        consultation = self._consultation_line(bill, today)
        if consultation:
            details.append(consultation)
        steps = self._steps_block(bill, today)
        if steps:
            details.append(steps)

        # Short one-line facts are grouped compactly; the paragraphs above are
        # separated by blank lines so they read as distinct blocks.
        meta_lines: list[str] = []
        act = bill.act
        if act is not None:
            # The card is the top of the thread and the one message a reader comes back to; the
            # Dziennik Ustaw address is how the act is cited everywhere else, and it was only
            # ever in the publication notice, months down the replies.
            published = esc(act.display_address)
            if act.promulgation_date:
                published += f" ({esc(lb.published_on)} {self.fmt_date(act.promulgation_date)})"
            meta_lines.append(self._field(ICON["journal"], lb.journal, published))
        last = bill.last_stage
        if last is not None:
            meta_lines.append(self._field(ICON["stage"], lb.stage, self._current_stage(bill, last)))
        elif bill.wykaz is not None:
            # "Zrealizowany" on the entry means the Council has adopted the draft: the plan
            # is behind it, and "текста ещё нет" was the one thing the line said.
            plan = lb.wykaz_stage_adopted if bill.wykaz.is_adopted else lb.wykaz_stage
            meta_lines.append(self._field(ICON["stage"], lb.stage, esc(plan)))
        elif bill.rcl is not None:
            meta_lines.append(self._field(ICON["stage"], lb.stage, esc(lb.rcl_no_stage)))
        elif bill.is_pre_print:
            meta_lines.append(self._field(ICON["stage"], lb.stage, esc(lb.pre_print_stage)))
        meta_lines.append(self._applicant_line(bill))
        if s.prints_considered_jointly:
            meta_lines.append(
                f"{esc(lb.joint_prints)} {esc(', '.join(s.prints_considered_jointly))}"
            )
        if any(h.startswith("text:") for h in bill.prefilter_hits):
            meta_lines.append(f"{ICON['search']} <i>{esc(lb.found_in_text)}</i>")
        note = self._text_note(bill)
        if note:
            meta_lines.append(f"{ICON['note']} <i>{esc(note)}</i>")
        details.append("\n".join(meta_lines))
        return "\n\n".join(details)

    def _card_links(self, bill: Bill, print_info: PrintInfo | None) -> list[str]:
        """Where the card points: the RCL project, the Sejm submission, or the process page
        with the print's PDF (and the RCL project a government print came from)."""
        lb = self._labels
        s = bill.summary
        if bill.wykaz is not None:
            return [
                link(bill.wykaz.web_url, lb.link_wykaz_entry),
                link(WYKAZ_REGISTER_URL, lb.link_wykaz),
            ]
        if bill.rcl is not None:
            return self._rcl_links(bill.rcl)
        if bill.is_pre_print:
            return [link(s.web_url, lb.link_submission_pdf)]
        if bill.act is not None:
            return self._act_links(bill, bill.act)
        links = [link(s.web_url, lb.link_process)]
        pdf = print_info.main_pdf if print_info else None
        if pdf is not None:
            links.append(link(pdf.url, lb.link_pdf))
        if s.rcl_link:
            # The government stage the print came through, and the door to everything the card
            # itself cannot carry once the thread is the druk's: the consultation letter with the
            # ministry's address, the dates it ran between, the OSR. A chip labelled "RCL" was a
            # link a reader had no reason to follow.
            links.append(link(s.rcl_link, lb.link_rcl_project))
        return links

    def _card_tags(self, bill: Bill, today: dt.date) -> str:
        """Each tag answers one search: this bill's whole thread, by importance, by topic, where
        an opinion can still be sent, about citizens of Ukraine, government projects before the
        Sejm, and the bills of the term.

        The only tag line not built by `_tags`: the card leads with the thread and carries four
        tags a reply has no place for.
        """
        lb = self._labels
        return " ".join(
            [
                self._thread_tags(bill),
                *self._topic_tags(bill),
                *([f"#{lb.tag_consultations}"] if consultation_open(bill, today) else []),
                *([f"#{lb.tag_rcl}"] if bill.rcl is not None else []),
                *([f"#{lb.tag_wykaz}"] if bill.wykaz is not None else []),
                *([] if bill.has_process is False else [self._term_tag(bill.summary.term)]),
            ]
        )

    def joint_bill(
        self,
        bill: Bill,
        primary: Bill,
        print_info: PrintInfo | None,
        *,
        today: dt.date | None = None,
    ) -> RenderedMessage:
        """Reply under `primary`'s card: `bill` is considered jointly with it and gets no card of
        its own. What it does, how it differs from the prints the reader has already read about,
        who submitted it, links.

        The verdict, the importance and the category stay the card's: one thread, one score. The
        difference is this print's own news and the reason the reply exists.
        """
        lb = self._labels
        s = bill.summary
        header = self._header(ICON["joint"], lb.joint_bill_header, bill)
        others = [primary.number, *(n for n in s.prints_considered_jointly if n != primary.number)]
        note = f"{ICON['note']} {esc(lb.joint_bill_note.format(numbers=', '.join(others)))}"
        facts = f"{note}\n{self._applicant_line(bill)}"
        links_block = self._links(self._card_links(bill, print_info))
        # Its own tag first, then the card's topic and thread tags: a search for either number
        # finds the reply, and the verdict it carries is the card's.
        tags = self._tags(primary, self._number_tag(bill))
        steps = self._steps_block(primary, today or self._today(), road=bill)
        return RenderedMessage(
            text=self._assemble(
                [header, facts],
                flexible=[self._joint_summary(bill), self._joint_differences(bill)],
                tail=[steps, links_block, tags],
            )
        )

    def _joint_summary(self, bill: Bill) -> str:
        """What this print does, in its own analysis' words. Empty when it has none: a print
        compared with nothing to say is still worth naming in the thread."""
        if bill.analysis is None:
            return ""
        summary = bill.analysis.analysis.summary.strip()
        if not summary:
            return ""
        return f"{ICON['about']} <b>{esc(self._labels.about)}</b>\n{esc(summary)}"

    def _joint_differences(self, bill: Bill) -> str:
        """How it differs from the others, as the model read it off the channel's descriptions.

        Empty when the comparison was never made or failed — the model was down, the group had
        nothing analysed to compare with — and then the reply is what it was before comparisons
        existed. `same_substance` leads, because for a reader the answer "the same bill in other
        words" is as much news as a list of differences, and it is the commoner one.
        """
        lb = self._labels
        if bill.joint is None:
            return ""
        comparison = bill.joint.comparison
        heading = lb.joint_same if comparison.same_substance else lb.joint_differs
        lines = [f"{ICON['joint_diff']} <b>{esc(heading)}</b>"]
        if comparison.summary.strip():
            lines.append(esc(comparison.summary.strip()))
        lines.extend(f"• {esc(d.strip())}" for d in comparison.differences if d.strip())
        return "\n".join(lines)

    def status_update(
        self, bill: Bill, change: StatusChange, *, today: dt.date | None = None
    ) -> RenderedMessage:
        lb = self._labels
        analysis = bill.analysis.analysis if bill.analysis else None
        event = update_event(change, bill)
        today = today or self._today()

        header = self._header(
            EVENT_ICON.get(event, ICON["update"]), self._event_header(change, event), bill
        )
        badge = ""
        if analysis is not None:
            badge = (
                f"{score_icon(analysis.score)} {importance_bar(analysis.score)} "
                f"{analysis.score}/5 — "
                f"{esc(lb.category_labels.get(analysis.category, analysis.category))}"
            )

        stage_lines = [f"• {self._stage_line(st, today)}" for st in told_stages(change.new_stages)]
        stages_block = (
            f"{ICON['new_stages']} <b>{esc(lb.new_stages)}</b>\n" + "\n".join(stage_lines)
            if stage_lines
            else ""
        )
        closure = self._closure_line(bill, change, event)
        consultation = self._consultation_line(bill, today)
        # `_closure_line` is the one place an update says how the road ended; `_steps_block` ends
        # in `_ended_line`, which says it again from the bill. An RCL project closed and a plan
        # dropped went out with the sentence twice, a blank line apart.
        over = (
            change.withdrawn
            or change.discontinued
            or (change.closure_detected and is_over(bill, today=today))
        )
        steps = "" if over else self._steps_block(bill, today)

        summary_block = ""
        changes_block = ""
        amendments_block = self._amendments_block(change)
        supplements_block = self._supplements_block(change)
        if analysis is not None:
            # The card carries the whole summary; a reply repeats one sentence of it, the whole
            # text only when the analysis itself changed.
            summary_block = self._summary_reminder(analysis.summary, full=change.content_changed)
            if change.content_changed:
                bullets = "\n".join(
                    f"• {esc(c.strip())}" for c in analysis.changes_since_previous if c.strip()
                )
                if bullets:
                    changes_block = (
                        f"{ICON['changed']} <b>{esc(lb.changes_since_previous)}</b>\n{bullets}"
                    )
                else:
                    changes_block = f"{ICON['note']} <i>{esc(lb.reanalyzed_note)}</i>"

        # Event tags only when the reply carries the event a reader would search for; `_tags`
        # adds the two halves every reply has.
        tags = self._tags(
            bill,
            *(
                f"#{lb.event_tags[key]}"
                for key in event_keys(change, event)
                if key in lb.event_tags
            ),
        )

        # The new stages are what the post is for, and stages held since the last one are listed
        # here and released against it: shrunk away, they would be marked told and never told.
        text = self._assemble(
            [header, badge, stages_block],
            flexible=[supplements_block, amendments_block, changes_block, summary_block],
            tail=[
                closure,
                consultation,
                steps,
                self._links(self._update_links(bill, change)),
                tags,
            ],
        )
        return RenderedMessage(text=text)

    def _closure_line(self, bill: Bill, change: StatusChange, event: str) -> str:
        """What ended or moved on: the closure line explains what the header only names; when
        the header already says "the Sejm passed/rejected the bill" the line would repeat it.
        A print assigned to an RPW/RCL entry is announced here too."""
        lb = self._labels
        closure = ""
        if change.discontinued:
            carried = bill.summary.applicant_type is ApplicantType.CITIZENS
            closure = f"{ICON['closed']} " + esc(
                lb.process_carried_over if carried else lb.process_discontinued
            )
        elif change.withdrawn:
            told = lb.process_withdrawn if event == "withdrawn" else lb.process_abandoned
            closure = f"{ICON['closed']} {esc(told)}"
        elif change.closure_detected and bill.wykaz is not None:
            closure = f"{ICON['closed']} {esc(lb.wykaz_process_closed)}"
        elif change.closure_detected and bill.rcl is not None:
            closure = f"{ICON['closed']} {esc(lb.rcl_process_closed)}"
        elif event == "veto_sustained":
            # The header names the outcome; the majority the Sejm needed is what explains it.
            closure = f"{ICON['closed']} {esc(lb.process_veto_sustained)}"
        elif (
            change.closure_detected
            and event not in _SELF_EXPLAINING_CLOSURES
            and veto_outcome(bill.stages) is None
        ):
            icon = ICON["passed"] if change.passed else ICON["closed"]
            closure = f"{icon} {esc(lb.process_passed if change.passed else lb.process_closed)}"
        if event == "print_assigned":
            assigned = f"{ICON['print']} {esc(lb.print_assigned)}: <b>{esc(bill.number)}</b>"
            closure = f"{assigned}\n{closure}" if closure else assigned
        elif bill.rcl is not None and bill.rcl.sent_to_sejm and reaches_sejm(change):
            closure = f"{ICON['print']} {esc(lb.rcl_sent_to_sejm)}"
        return closure

    def _update_links(self, bill: Bill, change: StatusChange) -> list[str]:
        """The process (or RCL project) page, the text the update is about, the amendments, and
        every document filed to the print that this update tells."""
        lb = self._labels
        s = bill.summary
        if bill.wykaz is not None:
            links = [link(bill.wykaz.web_url, lb.link_wykaz_entry)]
        else:
            label = lb.link_rcl_project if bill.rcl is not None else lb.link_process
            links = [link(s.web_url, label)]
        text_after3 = next((st.text_after3 for st in change.new_stages if st.text_after3), None)
        if text_after3:
            links.append(link(text_after3, lb.link_text_after3))
        elif bill.analysis and bill.analysis.source_url and change.content_changed:
            links.append(link(bill.analysis.source_url, lb.link_pdf))
        if change.amendments is not None and change.amendments.source_url:
            links.append(link(change.amendments.source_url, lb.link_amendments))
        for record in change.supplements:
            if record.source_url:
                label = lb.supplement_headers.get(record.source_kind, lb.link_supplement)
                links.append(link(record.source_url, label))
        return links

    def _supplements_block(self, change: StatusChange) -> str:
        """One `📄 Позиция правительства` heading per document filed to the print, with the
        model's digest under it. A document that could not be read keeps its heading and its
        link: the reader is told it exists."""
        lb = self._labels
        blocks = []
        for record in change.supplements:
            label = lb.supplement_headers.get(record.source_kind, record.title)
            lines = [f"{ICON['filed']} <b>{esc(label)}</b>"]
            digest = record.digest
            if digest is None:
                lines.append(esc(record.title))
                lines.append(f"<i>{esc(lb.supplement_unreadable)}</i>")
                blocks.append("\n".join(lines))
                continue
            verdict = lb.supplement_supports.get(_supports_key(digest.supports))
            if verdict:
                lines.append(f"<b>{esc(verdict)}</b>")
            if digest.summary.strip():
                lines.append(esc(digest.summary.strip()))
            lines.extend(f"• {esc(p.strip())}" for p in digest.points if p.strip())
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _amendments_block(self, change: StatusChange) -> str:
        """`🆕 Что меняют поправки Сената` with the model's summary and bullets."""
        record = change.amendments
        if record is None:
            return ""
        lb = self._labels
        label = (
            lb.amendments_senate
            if record.source_kind == "senate_amendments"
            else lb.amendments_committee
        )
        lines = [f"{ICON['changed']} <b>{esc(label)}</b>"]
        if record.amendments.summary.strip():
            lines.append(esc(record.amendments.summary.strip()))
        lines.extend(f"• {esc(c.strip())}" for c in record.amendments.changes if c.strip())
        return "\n".join(lines)

    def act_published(self, bill: Bill) -> RenderedMessage:
        lb = self._labels
        act = bill.act
        if act is None:
            raise ValueError(f"bill {bill.number} has no act")
        header = self._header(ICON["published"], lb.act_published_header, bill, act.title)
        lines = [self._field(ICON["journal"], lb.journal, esc(act.display_address))]
        if act.promulgation_date:
            lines[0] += f" ({esc(lb.published_on)} {self.fmt_date(act.promulgation_date)})"
        if act.entry_into_force is None:
            lines.append(f"{ICON['effective']} <i>{esc(lb.entry_into_force_unknown)}</i>")
        elif act.already_in_force_when_fetched:
            lines.append(
                f"{ICON['effective']} <b>{esc(lb.already_in_force_since)}</b> "
                f"{self.fmt_date(act.entry_into_force)}"
            )
        else:
            lines.append(
                self._field(
                    ICON["effective"], lb.enters_into_force, self.fmt_date(act.entry_into_force)
                )
            )
        lines.append(f"{ICON['note']} <i>{esc(lb.partial_vacatio_note)}</i>")
        facts = "\n".join(lines)
        # The moment a reader has to diarise the date, and the last one before the channel goes
        # quiet for the vacatio legis: without a sentence of the summary the notice was two dates
        # under a Polish act title, and the card it replies to is months up the thread.
        summary_block = ""
        if bill.analysis is not None:
            summary_block = self._summary_reminder(
                bill.analysis.analysis.summary, full=False, law=True
            )
        links_block = self._links(self._act_links(bill, act))
        tags = self._tags(bill, f"#{lb.tag_published}")
        text = self._assemble(
            [header, facts],
            flexible=[summary_block],
            tail=[self._action_line(bill, self._today()), links_block, tags],
        )
        return RenderedMessage(text=text)

    def in_force(self, bill: Bill, *, today: dt.date | None = None) -> RenderedMessage:
        lb = self._labels
        act = bill.act
        if act is None or act.entry_into_force is None:
            raise ValueError(f"bill {bill.number} has no entry-into-force date")
        # A missed run posts this late, and "from today" would then be the one thing that is not.
        on_the_day = act.entry_into_force == (today or self._today())
        label = lb.in_force_header if on_the_day else lb.in_force_header_dated
        header = self._header(ICON["in_force"], label, bill, act.title)
        facts = (
            f"{ICON['journal']} {esc(act.display_address)} · {esc(lb.in_force_since)} "
            f"{self.fmt_date(act.entry_into_force)}\n"
            f"{ICON['note']} <i>{esc(lb.partial_vacatio_note)}</i>"
        )
        summary_block = ""
        practical = ""
        if bill.analysis is not None:
            a = bill.analysis.analysis
            summary_block = (
                f"{ICON['about']} <b>{esc(lb.act_summary)}</b>\n{esc(a.summary.strip())}"
            )
            if a.practical_impact.strip():
                practical = self._field(
                    ICON["practical"], lb.practical_impact, esc(a.practical_impact.strip())
                )
        links_block = self._links(self._act_links(bill, act))
        tags = self._tags(bill, f"#{lb.tag_in_force}")
        text = self._assemble(
            [header, facts],
            flexible=[summary_block, practical],
            tail=[self._action_line(bill, today or self._today()), links_block, tags],
        )
        return RenderedMessage(text=text)

    def consultation_deadline(self, bill: Bill, *, today: dt.date) -> RenderedMessage:
        """Reply under the card a few days before the public consultation closes."""
        lb = self._labels
        window = bill.consultation
        if window is None or window.end is None:
            raise ValueError(f"bill {bill.number} has no consultation end date")
        header = self._header(ICON["consultation"], lb.consultation_deadline_header, bill)
        where = self._consultation_where(window, sejm_label=lb.consultation_hint)
        if bill.rcl is not None:
            # The card says how to write to a ministry; this reply is the one a reader acts on,
            # three days before the window shuts and weeks below the card in the thread.
            project = bill.rcl
            polish = lb.action_in_polish.format(wykaz=project.wykaz_number or project.number)
            where = f"{where} {esc(polish)}"
        until = (
            f"{esc(lb.consultation_until)} {self.fmt_date(window.end)} · "
            f"{self._countdown((window.end - today).days)}"
        )
        facts = (
            f"{self._field(ICON['effective'], lb.consultation, until)}\n{ICON['action']} {where}"
        )
        next_step = self._next_step_line(bill, today)
        summary_block = ""
        if bill.analysis is not None:
            a = bill.analysis.analysis
            summary_block = f"{ICON['about']} <b>{esc(lb.about)}</b>\n{esc(a.summary.strip())}"
        links_block = self._links(self._consultation_links(bill, window))
        tags = self._tags(bill, f"#{lb.tag_consultations}")
        text = self._assemble(
            [header, facts], flexible=[summary_block], tail=[next_step, links_block, tags]
        )
        return RenderedMessage(text=text)

    def consultation_results(self, bill: Bill, *, today: dt.date | None = None) -> RenderedMessage:
        """Reply under the card once the Sejm publishes the opinions received."""
        lb = self._labels
        window = bill.consultation
        if window is None:
            raise ValueError(f"bill {bill.number} had no public consultation")
        header = self._header(ICON["consultation"], lb.consultation_results_header, bill)
        page = window.form_url
        if bill.rcl is not None:
            facts = f"{ICON['note']} {link(bill.rcl.web_url, lb.rcl_results_hint)}"
        else:
            facts = f"{ICON['note']} " + (
                link(page, lb.consultation_results_hint)
                if page
                else esc(lb.consultation_results_hint)
            )
        if window.end:
            # A date range reads as an invitation, and this post exists because the window shut.
            closed = f"{esc(lb.consultation_closed_on)} {self.fmt_date(window.end)}"
            facts = f"{self._field(ICON['effective'], lb.consultation, closed)}\n{facts}"
        steps = self._steps_block(bill, today or self._today())
        links_block = self._links(self._consultation_links(bill, window))
        tags = self._tags(bill, f"#{lb.tag_consultation_results}")
        return RenderedMessage(text=self._assemble([header, facts, steps, links_block, tags]))

    def agenda(
        self,
        bill: Bill,
        item: AgendaItem,
        *,
        moved_from: AgendaItem | None = None,
        today: dt.date | None = None,
    ) -> RenderedMessage:
        """Reply under the card: the bill is on the agenda of a committee or Sejm sitting.

        `moved_from` is the sitting as last announced, not a bare date: 302 of the 886 committee
        sittings of term 10 that changed kept the day and moved the hour or the room, and
        «перенесено с 17.09.2026» over an unchanged date tells the reader nothing.
        """
        lb = self._labels
        s = bill.summary
        is_committee = item.kind == "committee"
        head_label = lb.agenda_committee_header if is_committee else lb.agenda_sejm_header
        header = self._header(ICON["calendar"], head_label, bill)
        lines: list[str] = []
        if is_committee:
            name = self._committee_display(bill, item.committee_code or "", item.committee_name)
            lines.append(f"{ICON['committee']} <b>{esc(name)}</b>")
            when = f"{ICON['effective']} {self._agenda_when(item)}"
            if item.room:
                when += f" · {esc(item.room)}"
            lines.append(when)
        else:
            lines.append(f"{ICON['stage']} {self._agenda_when(item)}")
        if moved_from is not None:
            lines.append(f"{ICON['note']} {self._moved_line(moved_from, item)}")
        lines.extend(self._sitting_notes(item))
        if item.text:
            lines.append(self._field(ICON["agenda"], lb.agenda_item, esc(item.text)))
        facts = "\n".join(lines)
        action = self._action_line(bill, today or self._today(), agenda_item=item)
        links = [link(s.web_url, lb.link_process)]
        if item.video_url:
            links.append(link(item.video_url, lb.link_video))
        if is_committee and item.committee_code:
            links.append(link(committee_web_url(s.term, item.committee_code), lb.link_committee))
        links_block = self._links(links)
        tags = self._tags(
            bill, f"#{lb.tag_committee_sitting if is_committee else lb.tag_sejm_sitting}"
        )
        summary_block = ""
        if bill.analysis is not None:
            summary_block = self._summary_reminder(bill.analysis.analysis.summary, full=False)
        text = self._assemble(
            [header, facts], flexible=[summary_block], tail=[action, links_block, tags]
        )
        return RenderedMessage(text=text)

    def agenda_cancelled(
        self, bill: Bill, item: AgendaItem, *, still_meets: bool, today: dt.date | None = None
    ) -> RenderedMessage:
        """Reply under the card: a sitting the channel announced is not happening as announced.

        Two different facts: the sitting is off, or it meets without this bill. A sitting that
        merely moved is neither — it keeps its `sitting_key` and is told as a new agenda post. The
        tag is the announcement's, so one search finds a sitting and its retraction together.
        """
        lb = self._labels
        today = today or self._today()
        is_committee = item.kind == "committee"
        header = self._header(
            ICON["calendar"],
            lb.agenda_dropped_header if still_meets else lb.agenda_cancelled_header,
            bill,
        )
        lines: list[str] = []
        if is_committee:
            name = self._committee_display(bill, item.committee_code or "", item.committee_name)
            lines.append(f"{ICON['committee']} <b>{esc(name)}</b>")
        lines.append(f"{ICON['effective']} {esc(lb.agenda_was_planned)} {self._agenda_when(item)}")
        lines.append(
            f"{ICON['note']} {esc(lb.agenda_dropped_note if still_meets else lb.agenda_off_note)}"
        )
        facts = "\n".join(lines)
        links = [link(bill.summary.web_url, lb.link_process)]
        if is_committee and item.committee_code:
            links.append(
                link(committee_web_url(bill.summary.term, item.committee_code), lb.link_committee)
            )
        tags = self._tags(
            bill, f"#{lb.tag_committee_sitting if is_committee else lb.tag_sejm_sitting}"
        )
        return RenderedMessage(
            text=self._assemble(
                [header, facts],
                tail=[self._next_step_line(bill, today), self._links(links), tags],
            )
        )

    def decision_deadline(self, bill: Bill, phase: Phase, *, today: dt.date) -> RenderedMessage:
        """Reply under the card as the Senate's or the President's constitutional term runs out.

        Between the Sejm's vote and the act in Dziennik Ustaw the channel is otherwise silent for
        up to seven weeks, and those are the reader's last two windows: the Senate's committee
        takes opinions, and the President can still veto."""
        lb = self._labels
        if phase.deadline is None:
            raise ValueError(f"bill {bill.number}: the {phase.key} phase carries no deadline")
        senate = phase.key == "senate"
        label = lb.senate_deadline_header if senate else lb.president_deadline_header
        header = self._header(ICON["deadline"], label, bill)
        line = (lb.senate_deadline_line if senate else lb.president_deadline_line).format(
            date=self.fmt_date(phase.deadline)
        )
        # The deadline itself is already the shortened one for an urgent bill (art. 123), and a
        # date two weeks earlier than every other bill's needs saying why: the normal body would
        # promise the Senate's committee sits "well before" a term that is half as long.
        if is_urgent(bill):
            body = lb.senate_deadline_body_urgent if senate else lb.president_deadline_body_urgent
        else:
            body = lb.senate_deadline_body if senate else lb.president_deadline_body
        facts = (
            f"{ICON['effective']} <b>{esc(line)}</b> · "
            f"{self._countdown((phase.deadline - today).days)}\n"
            f"{esc(body)}"
        )
        if not phase.deadline_exact:
            # Only the Senate's thirty days and a President's whose bill came through a vote on
            # the Senate's amendments are counted from the stage before the hand-over. After
            # `ToPresident` the API dates the hand-over itself, and apologising for a date that
            # is exact teaches the reader to discount it.
            facts += f"\n{ICON['note']} <i>{esc(lb.deadline_counted_from_vote)}</i>"
        links = [link(bill.summary.web_url, lb.link_process)]
        if senate and bill.senate is not None:
            links.append(link(bill.senate.url, lb.link_senate_act))
        elif senate:
            links.append(link(SENATE_BILLS_URL, lb.link_senate_bills))
        summary_block = ""
        if bill.analysis is not None:
            summary_block = self._summary_reminder(
                bill.analysis.analysis.summary, full=False, law=True
            )
        text = self._assemble(
            [header, facts],
            flexible=[summary_block],
            tail=[
                self._action_line(bill, today, when_none=False),
                self._links(links),
                self._tags(bill, f"#{lb.event_tags['senate' if senate else 'president']}"),
            ],
        )
        return RenderedMessage(text=text)

    def hearing_deadline(self, bill: Bill, hearing: Stage, *, today: dt.date) -> RenderedMessage:
        """Reply under the card a few days before applications to a public hearing close."""
        lb = self._labels
        deadline = hearing_application_deadline(hearing)
        if deadline is None or hearing.date is None:
            raise ValueError(f"bill {bill.number}: the hearing has no date")
        header = self._header(ICON["hearing"], lb.hearing_deadline_header, bill)
        when = f"{ICON['effective']} <b>{esc(lb.hearing_on)}</b> {self.fmt_date(hearing.date)}"
        if deadline < today:
            # Announced with less than the ten days art. 70b asks for: the hearing is still
            # ahead and worth telling, the application window never opened for the reader.
            facts = f"{when} · {esc(lb.hearing_applications_closed)}"
        else:
            facts = (
                f"{when} · {esc(lb.hearing_apply_until)} <b>{self.fmt_date(deadline)}</b> · "
                f"{self._countdown((deadline - today).days)}\n"
                f"{ICON['action']} {esc(lb.hearing_hint)}"
            )
            details = self._hearing_application_details(bill, hearing, deadline)
            if details:
                facts += f"\n{ICON['action']} {details}"
        links = [
            link(hearing_rules_url(bill.term), lb.link_hearing_rules),
            link(bill.summary.web_url, lb.link_process),
        ]
        phase = next_phase(bill, today=today)
        for code in phase.committees if phase else ():
            links.append(link(committee_web_url(bill.term, code), lb.link_committee))
        links_block = self._links(links)
        tags = self._tags(bill, f"#{lb.tag_hearing}")
        summary_block = ""
        if bill.analysis is not None:
            summary_block = self._summary_reminder(bill.analysis.analysis.summary, full=False)
        text = self._assemble([header, facts], flexible=[summary_block], tail=[links_block, tags])
        return RenderedMessage(text=text)

    def _act_links(self, bill: Bill, act: ActInfo) -> list[str]:
        lb = self._labels
        links = [link(bill.summary.web_url, lb.link_process)]
        isap = act.isap_url or bill.summary.isap_url
        if isap:
            links.append(link(isap, lb.link_isap))
        if act.text_pdf_url:
            links.append(link(act.text_pdf_url, lb.link_act_pdf))
        return links

    def run_report(
        self, report: RunReport, log_lines: list[str], *, run_url: str | None = None
    ) -> RenderedMessage:
        lb = self._labels
        status = "✅" if report.ok else "❌"
        duration = report.duration_seconds
        head = (
            f"<b>{status} {esc(lb.run_report_title)}</b>\n"
            f"mode: {esc(report.mode)} · since: {esc(report.since.strftime('%Y-%m-%d %H:%M'))}"
            + (f" · term {report.term}" if report.term is not None else "")
            + (f" · {duration}s" if duration is not None else "")
            + (f"\n{link(run_url, 'GitHub Actions log')}" if run_url else "")
        )
        # Zero counters say nothing: a section lists what happened, or that nothing did.
        sections = [
            _section(
                "🔎",
                "discovery",
                _counters(
                    ("Sejm: {} new", report.discovered),
                    # Spelled out, because this counter is usually the only one on the line and
                    # "+1 without print number" said neither where the bill is nor what it is.
                    (
                        "{} submitted to the Sejm, no druk number yet (RPW)",
                        report.pre_print_discovered,
                    ),
                    ("prefilter hits: {}", report.prefilter_hits),
                ),
                _counters(
                    ("RCL: {} new", report.rcl_discovered),
                    ("prefilter hits: {}", report.rcl_prefilter_hits),
                ),
                _counters(
                    ("wykaz prac RM: {} new", report.wykaz_discovered),
                ),
                _counters(
                    ("{} already over when first seen, not followed", report.over_on_arrival),
                ),
                _counters(
                    ("text prefilter: checked {}", report.text_prefilter_checked),
                    ("hits {}", report.text_prefilter_hits),
                    ("scans to the model {}", report.text_prefilter_scans),
                    ("unreadable {}", report.text_prefilter_unreadable),
                    ("no file to read yet, still pending {}", report.text_prefilter_unanswered),
                ),
                empty="nothing new",
            ),
            _section(
                "🤖",
                "analysis",
                _counters(
                    ("analyzed: {}", report.analyzed),
                    ("triaged out: {}", report.triaged_out),
                    ("failures: {}", report.analysis_failures),
                    ("over the cost limit: {}", report.analysis_skipped_cost),
                    ("file refused, still queued: {}", report.analysis_unanswered),
                    ("joint prints revived: {}", report.joint_revived),
                ),
                _counters(
                    ("отправлено запросов в batch: {}", report.batch_requests_submitted),
                    ("ожидают обработки результатов batch: {}", report.batch_requests_pending),
                    ("в очереди на отправку в batch: {}", report.batch_requests_queued),
                    ("отправка batch не подтверждена: {}", report.batch_requests_uncertain),
                ),
                _tokens_line(report),
                _spenders_line(report),
                empty="nothing analyzed",
            ),
            _section(
                "📣",
                "posts",
                _counters(
                    ("new cards: {}", report.published),
                    ("alternatives: {}", report.joint_published),
                    ("updates: {}", report.updates),
                    ("held: {}", report.held),
                    ("re-analyzed: {}", report.reanalyzed),
                    ("linked: {}", report.linked),
                    ("tracked: {}", report.tracked),
                    ("ждут ответа батча: {}", report.batch_waiting),
                ),
                _counters(
                    ("acts: {}", report.acts_published),
                    ("in force: {}", report.in_force_posted),
                    ("consultation reminders: {}", report.consultation_reminders),
                    ("results: {}", report.consultation_results_posted),
                    ("agenda: {}", report.agenda_posted),
                    ("sittings called off: {}", report.agenda_cancelled),
                    ("hearings: {}", report.hearing_reminders),
                    ("Senate/President deadline: {}", report.decision_reminders),
                ),
                _counters(
                    ("end of term: {} bill(s) lapsed", report.discontinued),
                    ("{} government row(s) carried over", report.rehomed),
                ),
                empty="nothing posted",
            ),
        ]
        timing = " · ".join(
            f"{esc(name)} {secs:.1f}s"
            for name, secs in report.phase_seconds.items()
            if f"{secs:.1f}" != "0.0"  # a phase that had nothing to do
        )
        if timing:
            sections.append(_section("⏱", "timing", timing))
        if report.commands:
            sections.append(_section("🛠", "commands", *(_bullet(c) for c in report.commands)))
        if report.errors:
            sections.append(_section("❌", "errors", *(_bullet(e) for e in report.errors)))
        if report.notes:
            sections.append(_section("ℹ️", "notes", *(_bullet(n) for n in report.notes)))
        rejected_text = ""
        if report.rejected:
            rejected_text = _section(
                "🗂",
                "analysed, not published",
                *(
                    f"• {_verdict_ref(v)} · {esc(v.reason)} · {esc(_clip(v.title, 110))}"
                    for v in report.rejected
                ),
            )
        logs = ""
        prefilter_blocks = []
        for unreadable, heading in (
            (False, "prefilter: rejected"),
            (True, "prefilter: unreadable, not a relevance verdict"),
        ):
            details = [
                v for v in report.prefilter_details if (v.stage == "unreadable") == unreadable
            ]
            if details:
                prefilter_blocks.append(
                    _section(
                        "🔍",
                        f"{heading} ({len(details)})",
                        *(
                            f"• {link(process_web_url(v.term, v.number), _number_ref(v.number))}"
                            f" · {esc(v.stage)} · {esc(_clip(v.title, 110))}\n"
                            f"  {esc(v.reason)}"
                            for v in details
                        ),
                    )
                )
        if log_lines:
            logs = "⚠️ <b>warnings</b>\n<pre>" + esc("\n".join(log_lines)) + "</pre>"
        text = self._assemble(
            [head, "\n\n".join(sections)], flexible=[*prefilter_blocks, rejected_text, logs]
        )
        return RenderedMessage(text=text)

    def backfill_report(self, report: BackfillReport) -> RenderedMessage:
        """What the text-prefilter backfill did, for the log channel: a step of its own before
        the run, so nothing it does shows in the run's report. The accepted rows are the point —
        they are what the run that follows pays to read."""
        status = "✅" if report.ok else "❌"
        duration = report.duration_seconds
        scope = "title and text skips" if report.include_text_skipped else "title skips"
        head = f"<b>{status} lexinform backfill</b>\nscope: {esc(scope)} · limit {report.limit}" + (
            f" · {duration}s" if duration is not None else ""
        )
        counts = _section(
            "🔁",
            "re-scanned",
            _counters(
                ("scanned: {}", report.scanned),
                ("queued for analysis: {}", len(report.accepted)),
            ),
            empty="nothing was skipped",
        )
        queued = ""
        if report.accepted:
            queued = _section(
                "📥",
                "queued — the next run reads these",
                *(
                    f"• {_number_ref(o.number)}"
                    + (f" · {esc(', '.join(o.hits))}" if o.hits else " · no keywords (a scan)")
                    + f" · {esc(_clip(o.title, 110))}"
                    for o in report.accepted
                ),
            )
        errors = (
            _section("❌", "errors", *(_bullet(e) for e in report.errors)) if report.errors else ""
        )
        text = self._assemble([head, counts], flexible=[queued, errors])
        return RenderedMessage(text=text)

    def digest(self, week: Digest, *, draft: bool = False) -> RenderedMessage:
        """The week in the channel; `draft` adds the note only the technical channel's copy has."""
        lb = self._labels
        head = (
            f"{ICON['digest']} <b>{esc(lb.digest_header)}</b>\n"
            f"{self.fmt_date(week.since)} — {self.fmt_date(week.until)}"
        )
        body = [
            self._digest_block(lb.digest_cards, week.cards, self._digest_card),
            self._digest_block(lb.digest_updates, week.updates, self._digest_update),
            self._digest_block(lb.digest_consultations, week.consultations, self._digest_window),
            self._digest_block(lb.digest_sittings, week.sittings, self._digest_sitting),
        ]
        if week.is_empty:
            body = [esc(lb.digest_quiet)]
        tail = [self._month_block(week.month), self._support_block(), esc(lb.tag_digest)]
        head_blocks = [head, DIGEST_DRAFT_NOTE] if draft else [head]
        figures = week.figures
        figure_lines = [f"<b>{esc(lb.digest_figures)}</b>"]
        if figures is not None:
            figure_lines.append(esc(lb.digest_found.format(figures.discovered)))
            if figures.filtered is not None:
                figure_lines.append(esc(lb.digest_filtered.format(figures.filtered)))
        figure_lines.append(esc(lb.digest_published.format(len(week.cards))))
        head_blocks.append("\n".join(figure_lines))
        return RenderedMessage(text=self._assemble(head_blocks, flexible=body, tail=tail))

    def _digest_block[T](self, title: str, items: Sequence[T], row: Callable[[T], str]) -> str:
        """One section, capped, and saying how many it left out: a week of 14 cards showed ten
        and told the reader nothing about the other four."""
        lines = [text for item in items[:DIGEST_MAX_PER_SECTION] if (text := row(item))]
        if not lines:
            return ""
        left = len(items) - len(lines)
        if left > 0:
            lines.append(f"• {esc(self._labels.digest_and_more.format(left))}")
        return "\n".join([f"<b>{esc(title)}</b>", *lines])

    def _digest_card(self, entry: DigestEntry) -> str:
        score = f"{score_icon(entry.score)} {entry.score}/5 " if entry.score is not None else ""
        return f"• {score}{self._digest_ref(entry)} — {esc(_clip(entry.title, DIGEST_TITLE_CHARS))}"

    def _digest_update(self, entry: DigestEntry) -> str:
        """What the reply said, by its event where it has one and by its kind where it has not."""
        lb = self._labels
        by_kind = {
            PublicationKind.ACT_PUBLISHED: lb.act_published_header,
            PublicationKind.IN_FORCE: lb.in_force_header,
            PublicationKind.CONSULTATION_RESULTS: lb.consultation_results_header,
        }
        said = lb.update_headers.get(entry.event) or by_kind.get(entry.kind) or lb.update_header
        return f"• {self._digest_ref(entry)} — {esc(said)}"

    def _digest_window(self, window: Upcoming) -> str:
        """The section a reader acts on, so it says what the bill is: a bare number and a date
        told them nothing, and a numeric channel gives the number no link either."""
        left = ""
        if window.deadline is not None:
            days = (window.deadline - self._today()).days
            left = f" · {self.fmt_date(window.deadline)} · {self._countdown(days)}"
        title = esc(_clip(window.title, DIGEST_TITLE_CHARS))
        return f"• {self._digest_ref(window)} — {title}{left}"

    def _digest_sitting(self, window: Upcoming) -> str:
        """A sitting the committee called conditionally is marked as the card marks it: 21 of
        term 10 happen only if the Sejm refers something first, and a digest that states one as
        a settled date is the reason the rule exists. A plenary sitting names itself in
        `_agenda_when`, so naming it here as well said «Заседание Сейма, Заседание Сейма 65»."""
        item = window.sitting
        if item is None:
            return ""
        where = (
            f"{esc(item.committee_name or item.committee_code)}, " if item.committee_code else ""
        )
        mark = f" ({esc(self._labels.agenda_conditional_short)})" if item.condition else ""
        return f"• {self._digest_ref(window)} — {where}{self._agenda_when(item)}{mark}"

    def _digest_ref(self, entry: DigestEntry | Upcoming) -> str:
        """How the digest names a bill: its number as its own card names it (a government
        project by its wykaz number), linked to its post where the channel has a public name."""
        label = _number_ref(entry.number)
        government = is_rcl_number(entry.number) or is_wykaz_number(entry.number)
        if entry.wykaz_number and government:
            label = esc(entry.wykaz_number)
        url = self._post_url(entry.message_id)
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>' if url else label

    def _post_url(self, message_id: int | None) -> str | None:
        """A post of the channel by its id; only a channel with a public name has one."""
        if message_id is None or not self._channel.startswith("@"):
            return None
        return f"https://t.me/{self._channel[1:]}/{message_id}"

    def _month_block(self, month: MonthFigures | None) -> str:
        """What the month caught and what it cost; the first digest of a month carries it."""
        if month is None:
            return ""
        lb = self._labels
        rows = [
            f"{esc(lb.digest_month_discovered)}: {month.discovered}",
            f"{esc(lb.digest_month_dropped)}: {month.dropped_by_keywords}",
            f"{esc(lb.digest_month_analyzed)}: {month.analyzed}",
            f"{esc(lb.digest_month_published)}: {month.published}",
        ]
        cost = cost_usd(month.usage)
        if cost is not None and month.usage:
            rows.append(f"{esc(lb.digest_month_cost)}: ≈ {format_usd(cost)}")
        return (
            f"{ICON['month']} <b>{esc(lb.digest_month)}</b> · {month.month:%m.%Y}\n"
            + " · ".join(rows)
        )

    def _support_block(self) -> str:
        """The ask, and only where there is somewhere to send it."""
        lb = self._labels
        links = [
            link(url, label)
            for url, label in (
                (self._support_url, lb.digest_support_coffee),
                (self._sponsor_url, lb.digest_support_sponsor),
            )
            if url
        ]
        if not links:
            return ""
        return f"{ICON['support']} {esc(lb.digest_support)} " + " · ".join(links)

    def command_reply(self, command: IncomingCommand, outcome: CommandOutcome) -> RenderedMessage:
        """The answer to an operator command, English like the run report: what the bot knows
        about the bill, the verdict, and what happened to the card."""
        icon = {
            OutcomeStatus.ANALYSED: "🤖",
            OutcomeStatus.SKIPPED: "⏭",
            OutcomeStatus.SHOWN: "🔎",
            OutcomeStatus.SILENCED: "🔇",
            OutcomeStatus.QUEUED: "🔁",
            OutcomeStatus.RESET: "🔁",
            OutcomeStatus.REPUBLISHED: "📣",
            OutcomeStatus.FORGOTTEN: "🗑",
            OutcomeStatus.PREVIEWED: "👁",
            OutcomeStatus.REFRESHED: "🔄",
            OutcomeStatus.FOUND: "🔍",
            OutcomeStatus.REPORTED: "📊",
            OutcomeStatus.DELIVERY: "📨",
            OutcomeStatus.LISTED: "🏃",
            OutcomeStatus.SPENT: "💸",
            OutcomeStatus.HELP: "🛠",
            OutcomeStatus.EXECUTED_EARLIER: "🕗",
        }.get(outcome.status, "❌")
        quoted = esc(_clip(command.text, QUOTED_LINE_CHARS))
        head = f"{icon} <b>{esc(outcome.status)}</b> · <code>{quoted}</code>"
        if outcome.status is OutcomeStatus.HELP:
            body = [esc(outcome.note), COMMAND_HELP] if outcome.note else [COMMAND_HELP]
            return RenderedMessage(text=self._assemble([head, *body]))
        blocks = [head]
        bill = outcome.bill
        if bill is not None:
            blocks.append(self._bill_facts(bill, full=outcome.status in FULL_FACTS))
        if outcome.message_id is not None:
            blocks.append(f"📣 card posted: message {outcome.message_id}")
        if outcome.note:
            blocks.append(esc(outcome.note))
        return RenderedMessage(
            text=self._assemble(
                blocks, flexible=self._command_body(outcome), tail=[_command_cost(outcome)]
            )
        )

    def _command_body(self, outcome: CommandOutcome) -> list[str]:
        """What a command answers with beyond its verdict: a rendered card, a list of matches,
        the state of the queues. These are what gets shrunk when the message is too long."""
        bill = outcome.bill
        if outcome.delivery is not None:
            pub = outcome.delivery
            plan = pub.delivery
            lines = [
                f"publication {pub.id} · {esc(pub.number)} · {esc(pub.kind)} · {esc(pub.status)}",
                f"channel: {esc(pub.channel_id)} · created: {esc(pub.created_at.isoformat())}",
                f"message: {pub.message_id} · parent: {plan.reply_to if plan else None}",
                f"saved payload replayable: {bool(plan and plan.replayable(pub.kind))}",
            ]
            lines += [
                f"{esc(r.resolved_at.isoformat())} · {esc(r.action)} · "
                f"command {esc(r.command.chat_id)}/{r.command.message_id} "
                f"(update {r.command.update_id}) · "
                f"actor: {esc(r.command.actor_id or r.command.author_signature or 'unavailable')}"
                f" · {esc(r.reason)}"
                for r in outcome.delivery_history
            ]
            preview = self.delivery_preview(pub)
            return ["\n".join(lines), "<b>Saved snapshot preview</b>\n" + preview]
        if outcome.status is OutcomeStatus.PREVIEWED and bill is not None:
            if outcome.joint_primary is not None:
                return [self.joint_bill(bill, outcome.joint_primary, outcome.print_info).text]
            return [self.new_bill(bill, outcome.print_info).text]
        if outcome.as_json and bill is not None and bill.analysis is not None:
            return [f"<pre>{esc(bill.analysis.model_dump_json(indent=2))}</pre>"]
        if outcome.found:
            return ["\n".join(f"• {self._bill_line(b)}" for b in outcome.found)]
        if outcome.snapshot is not None:
            return [self._snapshot_body(outcome.snapshot)]
        if outcome.runs:
            return ["\n".join(_run_line(report) for report in outcome.runs)]
        if outcome.spend is not None:
            return [self._spend_body(outcome.spend)]
        return []

    def delivery_preview(self, pub: Publication) -> str:
        plan = pub.delivery
        if plan is None:
            return "No saved payload; retry unavailable."
        bill = Bill.model_validate_json(plan.bill_json)
        kind = pub.kind
        info = PrintInfo.model_validate_json(plan.print_json) if plan.print_json else None
        if kind is PublicationKind.NEW_BILL:
            return self.new_bill(bill, info).text
        if kind is PublicationKind.JOINT_BILL and plan.primary_json:
            return self.joint_bill(bill, Bill.model_validate_json(plan.primary_json), info).text
        if kind is PublicationKind.STATUS_UPDATE and plan.change_json:
            return self.status_update(bill, StatusChange.model_validate_json(plan.change_json)).text
        if kind is PublicationKind.ACT_PUBLISHED:
            return self.act_published(bill).text
        if kind is PublicationKind.CONSULTATION_RESULTS:
            return self.consultation_results(bill).text
        if kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED) and plan.item_json:
            item = AgendaItem.model_validate_json(plan.item_json)
            if kind is PublicationKind.AGENDA_CANCELLED:
                return self.agenda_cancelled(bill, item, still_meets=plan.still_meets).text
            moved = (
                AgendaItem.model_validate_json(plan.moved_from_json)
                if plan.moved_from_json
                else None
            )
            return self.agenda(bill, item, moved_from=moved).text
        if plan.today is not None:
            if kind is PublicationKind.IN_FORCE:
                return self.in_force(bill, today=plan.today).text
            if kind is PublicationKind.CONSULTATION_DEADLINE:
                return self.consultation_deadline(bill, today=plan.today).text
            if kind is PublicationKind.DECISION_DEADLINE and plan.phase_json:
                return self.decision_deadline(
                    bill, Phase.model_validate_json(plan.phase_json), today=plan.today
                ).text
            if kind is PublicationKind.HEARING_DEADLINE and plan.hearing_json:
                return self.hearing_deadline(
                    bill, Stage.model_validate_json(plan.hearing_json), today=plan.today
                ).text
        return "Saved payload is incomplete; retry unavailable."

    def _spend_body(self, spend: SpendSnapshot) -> str:
        """What `/cost` shows: the total, the models behind it, the dearest run and bills."""
        total = cost_usd(spend.usage)
        per_run = total / spend.runs if total is not None and spend.runs else None
        lines = [
            f"💸 <b>{format_usd(total)}</b> over {spend.runs} run(s) in {spend.days} days"
            f" · {format_usd(per_run)} per run"
        ]
        lines += [
            f"• <b>{esc(model.removeprefix('claude-'))}</b>: in {format_tokens(spent.input)}"
            f" · cache {format_tokens(spent.cache_read)}/{format_tokens(spent.cache_creation)}"
            f" · out {format_tokens(spent.output)} · {format_usd(cost_usd({model: spent}))}"
            for model, spent in sorted(spend.usage.items())
        ]
        if (dearest := spend.dearest) is not None:
            lines.append(
                f"🔺 dearest run {dearest.started_at:%d.%m %H:%M} UTC ·"
                f" {format_usd(cost_usd(dearest.llm_usage))} · {dearest.analyzed} analysed"
            )
        lines += [f"• {self._priciest_line(bill)}" for bill in spend.priciest]
        return "\n".join(lines)

    def _priciest_line(self, bill: Bill) -> str:
        """One of `/cost`'s dearest analyses: what was read for the bill, and what it cost."""
        record = bill.analysis
        if record is None:
            return self._bill_line(bill)
        spent = format_usd(cost_usd(usage_of(record)))
        return (
            f"<b>{self._number_label(bill)}</b> · {format_tokens(record.input_tokens or 0)} in"
            f" · {spent} · "
            + link(process_web_url(bill.term, bill.number), _clip(bill.summary.title, 60))
        )

    def _bill_line(self, bill: Bill) -> str:
        """One bill in a list: its number, where our pipeline left it, and its title as a link."""
        return f"<b>{self._number_label(bill)}</b> · {esc(bill.status)} · " + link(
            process_web_url(bill.term, bill.number), _clip(bill.summary.title, 90)
        )

    def _snapshot_body(self, snapshot: StatusSnapshot) -> str:
        lines = [
            f"📥 <b>bills</b>: {_counts(snapshot.bills)}",
            f"📣 <b>posts</b>: {_counts(snapshot.publications)}",
            f"👁 <b>followed</b>: {snapshot.followed}",
            _runs_line(snapshot),
        ]
        if snapshot.waiting:
            lines.append(
                "⏳ <b>waiting</b>\n"
                + "\n".join(f"• {self._bill_line(b)}" for b in snapshot.waiting)
            )
        if snapshot.stuck:
            lines.append(
                "🛑 <b>stuck</b>\n" + "\n".join(f"• {self._stuck_line(p)}" for p in snapshot.stuck)
            )
        if snapshot.batches or snapshot.queued_intents or snapshot.uncertain_intents:
            lines.append(self._batches_block(snapshot))
        if snapshot.awaiting_batch and snapshot.observed_at is not None:
            lines.append(
                "⏳ <b>Ждут сводки батча</b>\n"
                + "\n".join(
                    f"• <b>{esc(b.number)}</b> · "
                    f"{(snapshot.observed_at - b.awaiting_batch_since).total_seconds() / 3600:.1f}"
                    " ч"
                    for b in snapshot.awaiting_batch
                    if b.awaiting_batch_since is not None
                )
            )
        if snapshot.orphaned:
            lines.append(
                "⚠️ <b>batch_pending, held by no batch</b>\n"
                + "\n".join(f"• {_orphan_line(b)}" for b in snapshot.orphaned)
            )
        return "\n".join(lines)

    def _batches_block(self, snapshot: StatusSnapshot) -> str:
        """Open batches with their age, and the requests not yet (or not surely) submitted."""
        head = f"🧺 <b>batches</b>: {len(snapshot.batches)} open"
        if snapshot.queued_intents:
            head += f" · {snapshot.queued_intents} queued"
        if snapshot.uncertain_intents:
            head += f" · {snapshot.uncertain_intents} uncertain → lexinform batch-intents"
        return "\n".join(
            [head]
            + [
                f"• {esc(b.provider)} <code>{esc(b.batch_id)}</code> · {b.request_count} request(s)"
                f" · {esc(b.status)} since {self.fmt_date(b.submitted_at.date())}"
                for b in snapshot.batches
            ]
        )

    def _stuck_line(self, pub: Publication) -> str:
        """Ambiguous deliveries require an explicit operator decision before retrying."""
        age = self.fmt_date(pub.created_at.date())
        fix = f" → /delivery {pub.id}"
        return f"<b>{esc(pub.number)}</b> · {esc(pub.kind)} · {esc(pub.status)} since {age}{fix}"

    def _bill_facts(self, bill: Bill, *, full: bool) -> str:
        """The bill's title and link, its verdict, and (for /show) its status and last stage."""
        lines = [
            f"<b>{self._number_label(bill)}</b> · "
            + link(process_web_url(bill.term, bill.number), _clip(bill.summary.title, 160))
        ]
        if full:
            hits = ", ".join(bill.prefilter_hits) or "none"
            lines.append(f"status: {esc(bill.status)} · prefilter hits: {esc(hits)}")
            if bill.last_error:
                lines.append(f"last error: {esc(_clip(bill.last_error, 200))}")
            last = bill.last_stage
            if last is not None:
                when = f"{last.date} " if last.date else ""
                lines.append(f"last stage: {esc(when)}{esc(last.stage_name)}")
            window = bill.consultation
            if window is not None and window.end is not None:
                lines.append(f"consultation until {window.end}")
            last = bill.last_stage
            if last is not None:
                lines.append(
                    self._field(ICON["stage"], self._labels.stage, self._current_stage(bill, last))
                )
            today = self._today()
            lines.extend(
                line
                for line in (self._next_step_line(bill, today), self._action_line(bill, today))
                if line
            )
        record = bill.analysis
        if record is None:
            lines.append("not analysed")
        else:
            a = record.analysis
            verdict = "relevant" if a.relevant else "not relevant"
            lines.append(
                f"{score_icon(a.score)} {verdict} · importance {a.score}/5"
                f" · {esc(a.category)} · {esc(record.model)} · {esc(record.text_source)}"
            )
            if a.relevant:
                lines.append(f"<i>{esc(lead(a.summary))}</i>")
        return "\n".join(lines)

    def fmt_date(self, value: dt.date) -> str:
        return value.strftime(self._labels.date_format)

    def _header(self, icon: str, label: str, bill: Bill, title: str | None = None) -> str:
        """`📜 <b>Label — druk nr 3039</b>` and the bill's title on its own line."""
        return (
            f"{icon} <b>{esc(label)} — {self._number_label(bill)}</b>\n\n"
            f"<b>{esc(title or bill.summary.title)}</b>"
        )

    @staticmethod
    def _field(icon: str, label: str, value: str) -> str:
        """`🏷 <b>Label:</b> value`; `value` is HTML already."""
        return f"{icon} <b>{esc(label)}:</b> {value}"

    @staticmethod
    def _links(links: list[str]) -> str:
        return f"{ICON['links']} " + " | ".join(links)

    def _tags(self, bill: Bill, *lead: str) -> str:
        """Every reply's tag line: what the reply is about, then what the bill is about and its
        thread. `bill` is the bill the thread belongs to — for a reply under another print's card,
        that card's bill. One method because a reader following a topic must find the moments to
        act and not only the card; the card builds its own (`_card_tags`).
        """
        return " ".join([*lead, *self._topic_tags(bill), self._thread_tags(bill)])

    def _topic_tags(self, bill: Bill) -> list[str]:
        lb = self._labels
        record = bill.analysis
        if record is None:
            return []
        a = record.analysis
        return [
            f"#{lb.tag_importance}{a.score}",
            f"#{lb.category_tags.get(a.category, a.category)}",
            *([f"#{lb.tag_ukraine}"] if about_ukraine(bill) else []),
        ]

    def _countdown(self, days_left: int) -> str:
        lb = self._labels
        if days_left <= 0:
            return esc(lb.consultation_last_day)
        return f"{esc(lb.consultation_days_left)}: {days_left}"

    def _event_header(self, change: StatusChange, event: str) -> str:
        """The header names the event; an RCL stage is named after the stage itself."""
        lb = self._labels
        if event == "rcl_stage":
            stage = next(
                (st for st in reversed(change.new_stages) if st.stage_type == RCL_STAGE_TYPE), None
            )
            label = self._translate_stage(stage) if stage is not None else None
            if label:
                return label[0].upper() + label[1:]
        return lb.update_headers.get(event) or lb.update_header

    def _summary_reminder(self, summary: str, *, full: bool, law: bool = False) -> str:
        """`📝 Суть проекта: <first sentence>` or, after a re-analysis, the whole summary.

        `law` for the messages that come after the third reading — the Dziennik Ustaw notice and
        the two constitutional deadlines, whose own headers already say «Закон в Сенате». What
        the Sejm passed is an ustawa, and «Суть проекта» said of it is one word behind the story.
        """
        lb = self._labels
        text = summary.strip()
        if not text:
            return ""
        label = lb.act_summary if law else lb.current_summary
        if full:
            return f"{ICON['about']} <b>{esc(label)}</b>\n{esc(text)}"
        return f"{ICON['about']} <b>{esc(label)}:</b> {esc(lead(text))}"

    def _number_label(self, bill: Bill) -> str:
        if bill.wykaz is not None:
            return esc(bill.wykaz.number)
        if bill.rcl is not None:
            return esc(bill.rcl.wykaz_number or bill.number)
        if bill.is_pre_print:
            return f"{esc(bill.number)} ({esc(self._labels.no_print_yet)})"
        return f"{esc(self._labels.print_number)} {esc(bill.number)}"

    def _term_tag(self, term: int) -> str:
        """Only on cards: a search for it lists every bill of the term, without the replies."""
        return f"#{self._labels.tag_term}{term}"

    @staticmethod
    def _number_tag(bill: Bill) -> str:
        """One tag per bill, unique across terms: print numbers restart with every kadencja,
        RPW numbers carry the year already, wykaz numbers (UC164) are unique per government."""
        wykaz = bill.rcl.wykaz_number if bill.rcl is not None else None
        return _number_tag(bill.term, bill.number, wykaz)

    @staticmethod
    def _thread_tags(bill: Bill) -> str:
        """The bill's tag and, once an RCL/RPW entry and its druk are linked, the other one's:
        a search for either tag then finds the whole thread, card and replies alike."""
        tags = [MessageFormatter._number_tag(bill)]
        if bill.linked_number:
            tags.append(_number_tag(bill.term, bill.linked_number, bill.linked_wykaz_number))
        # Both rows of a linked pair carry the wykaz number, so their tags coincide: the project
        # and the plan it continues are one thread and must not be tagged twice.
        return " ".join(dict.fromkeys(tags))

    def _authors_suffix(self, bill: Bill) -> str:
        """ " (KO 17, Lewica 12 · представитель: Jan Kowalski, KO)" for deputies' bills."""
        a = bill.authors
        if a is None:
            return ""
        lb = self._labels
        parts: list[str] = []
        if a.clubs:
            clubs = ", ".join(f"{esc(club)} {n}" for club, n in a.clubs[:CLUBS_PER_SIDE])
            if len(a.clubs) > CLUBS_PER_SIDE:
                clubs += ", …"
            parts.append(f"{esc(lb.signatories)}: {clubs}")
        if a.representative:
            rep = esc(a.representative)
            if a.representative_club:
                rep += f", {esc(a.representative_club)}"
            parts.append(f"{esc(lb.representative)}: {rep}")
        return f" ({' · '.join(parts)})" if parts else ""

    def _applicant_line(self, bill: Bill) -> str:
        """Two lines: "Инициатор: правительственный — Minister … · номер в wykazie: UC164" and
        "Опубликован на RCL: 31.08.2026" for RCL projects; applicant with signatories, then the
        document date otherwise. The date has its own line: the applicant line with signatories
        is long and the date was lost at its end."""
        lb = self._labels
        s = bill.summary
        applicant = esc(lb.applicant_labels.get(s.applicant_type, s.applicant_type))
        if bill.rcl is not None:
            project = bill.rcl
            who = f"{applicant} — {esc(project.applicant)}"
            if project.wykaz_number:
                who += f" · {esc(lb.rcl_wykaz)}: {esc(project.wykaz_number)}"
            when = f"{ICON['doc_date']} <b>{esc(lb.rcl_published)}:</b> "
            return f"{ICON['applicant']} <b>{esc(lb.applicant)}:</b> {who}\n{when}" + esc(
                self.fmt_date(project.created)
            )
        if bill.wykaz is not None:
            entry = bill.wykaz
            who = applicant
            if entry.organ:
                # The register abbreviates the ministry and nothing else names it: a reader who
                # has to write there needs the office's name and its site, not "MSWiA".
                name = esc(ministry_name(entry.organ))
                url = ministry_url(entry.organ)
                who += f" — {link(url, name) if url else name}"
            who += f" · {esc(lb.rcl_wykaz)}: {esc(entry.number)}"
            when = esc(self.fmt_date(entry.published_at.date()))
            return (
                f"{ICON['applicant']} <b>{esc(lb.applicant)}:</b> {who}\n"
                f"{ICON['doc_date']} <b>{esc(lb.wykaz_published)}:</b> {when}"
            )
        doc_date = self.fmt_date(s.document_date) if s.document_date else "—"
        date_label = lb.received if bill.is_pre_print else lb.document_date
        return (
            f"{ICON['applicant']} <b>{esc(lb.applicant)}:</b> {applicant}"
            f"{self._authors_suffix(bill)}\n"
            f"{ICON['doc_date']} <b>{esc(date_label)}:</b> {esc(doc_date)}"
        )

    def _text_note(self, bill: Bill) -> str:
        """Why the analysis saw less than the whole text, if it did."""
        lb = self._labels
        record = bill.analysis
        if record is None:
            return ""
        if record.text_source == "metadata_only":
            if bill.wykaz is not None:
                return lb.wykaz_metadata_note
            if bill.rcl is not None:
                return lb.rcl_metadata_note
            return lb.pre_print_note if bill.is_pre_print else lb.partial_text_note
        return lb.partial_text_note if record.truncated else ""

    def _rcl_links(self, project: RclProject) -> list[str]:
        lb = self._labels
        links = [link(project.web_url, lb.link_rcl_project)]
        documents = project.text_documents()
        if "bill" in documents:
            doc = documents["bill"]
            links.append(link(doc.url, f"{lb.link_bill_text} ({doc.extension.upper()})"))
        if "justification" in documents:
            links.append(link(documents["justification"].url, lb.link_justification))
        if "osr" in documents:
            links.append(link(documents["osr"].url, lb.link_osr))
        if project.wykaz_url:
            # RCL's "Numer z wykazu" often points at a ministry's own register of draft
            # regulations, which is not the wykaz prac RM and does not list this bill.
            register = project.wykaz_url.startswith(WYKAZ_REGISTER_URL)
            links.append(
                link(project.wykaz_url, lb.link_wykaz if register else lb.link_ministry_plan)
            )
        return links

    def _consultation_line(self, bill: Bill, today: dt.date) -> str:
        window = bill.consultation
        if window is None:
            return ""
        lb = self._labels
        # A date range, like a bare "until <date>", reads as an invitation; a consultation that
        # is over says so and drops the ways to send an opinion (the letter stays: it names the
        # ministry and its e-mail). The end date is what the reader needs, so the range collapses
        # to it — and when the letter gave none, the timeline is what says the stage is behind.
        if window.end is not None and not window.is_open(today):
            closed: str | None = f"{esc(lb.consultation_closed_on)} {self.fmt_date(window.end)}"
        elif window.end is None and not consultation_open(bill, today):
            closed = esc(lb.consultation_closed_undated)
        else:
            closed = None
        if closed is not None:
            where = ""
            if window.source == "sejm" and window.form_url:
                # The page stays (the opinions that were sent appear there), the invitation goes.
                where = link(window.form_url, lb.consultation_page)
            elif window.source == "rcl" and window.letter_url:
                where = link(window.letter_url, lb.consultation_letter)
            return f"{ICON['consultation']} <b>{esc(lb.consultation)}:</b> {closed}" + (
                f" · {where}" if where else ""
            )
        if window.source == "rcl":
            when = (
                self._rcl_deadline(bill, window)
                if window.end is not None
                else esc(lb.consultation_deadline_in_letter)
            )
        elif window.end is not None:
            when = self._consultation_period(window)
        else:
            return ""
        return (
            f"{ICON['consultation']} <b>{esc(lb.consultation)}:</b> {when} · "
            f"{self._consultation_where(window)}"
        )

    def _rcl_deadline(self, bill: Bill, window: ConsultationWindow) -> str:
        """ "до 08.09.2026 (7 дн. с даты письма)"."""
        lb = self._labels
        assert window.end is not None, "the caller dates an RCL window only when its end is known"
        text = f"{esc(lb.consultation_until)} {self.fmt_date(window.end)}"
        days = bill.rcl.consultation.days if bill.rcl and bill.rcl.consultation else None
        if days is not None:
            text += f" ({esc(lb.consultation_days_from_letter.format(days=days))})"
        return text

    def _consultation_where(
        self, window: ConsultationWindow, *, sejm_label: str | None = None
    ) -> str:
        """Where an opinion goes: the Sejm form, or the ministry's e-mail and the letter (RCL)."""
        lb = self._labels
        if window.source == "sejm":
            # The survey is the thing to fill in; the project page only links to it.
            form = window.survey_url or window.form_url
            label = sejm_label or lb.consultation_link
            return link(form, label) if form else esc(lb.consultation_hint)
        parts: list[str] = []
        if window.email:
            parts.append(f"{esc(lb.consultation_email)} {esc(window.email)}")
        if window.letter_url:
            parts.append(link(window.letter_url, lb.consultation_letter))
        return " · ".join(parts) if parts else esc(lb.consultation_deadline_in_letter)

    def _consultation_links(self, bill: Bill, window: ConsultationWindow) -> list[str]:
        lb = self._labels
        if bill.rcl is not None:
            links = [link(bill.rcl.web_url, lb.link_rcl_project)]
            if window.letter_url:
                links.insert(0, link(window.letter_url, lb.consultation_letter))
            return links
        links = [link(bill.summary.web_url, lb.link_process)]
        if window.form_url:
            links.insert(0, link(window.form_url, lb.consultation_page))
        if window.survey_url:
            links.insert(0, link(window.survey_url, lb.consultation_link))
        return links

    def _hearing_application_details(self, bill: Bill, hearing: Stage, deadline: dt.date) -> str:
        """The filing route published in the committee agenda, when the API supplies one."""
        item = next(
            (
                agenda
                for agenda in bill.agenda
                if agenda.apply_email
                and (agenda.apply_by == deadline or agenda.date == hearing.date)
            ),
            None,
        )
        if item is None:
            lb = self._labels
            return esc(lb.hearing_application_details.format(email=HEARING_APPLICATION_EMAIL))
        return esc(f"{self._labels.agenda_apply} {item.apply_email}")

    def _consultation_period(self, window: ConsultationWindow) -> str:
        lb = self._labels
        assert window.end is not None, (
            "the caller asks for a period only when the window's end is known"
        )
        if window.start:
            return f"{self.fmt_date(window.start)} — {self.fmt_date(window.end)}"
        return f"{esc(lb.consultation_until)} {self.fmt_date(window.end)}"

    def _steps_block(self, bill: Bill, today: dt.date, *, road: Bill | None = None) -> str:
        """Where the bill is on its path, "what comes next" (dated when a sitting is scheduled,
        else with the usual duration) and "what you can do" — or, when the road has ended, the one
        sentence that says so. `road` is whose road it is when that is not whose step it is: a
        reply under a joint print's card takes the step from the card and the road from its own
        print, or the President's druk 316 inherits a «план ✓ → RCL ✓» it never passed through.
        """
        lines = [
            self._path_line(bill, today, road=road or bill),
            self._next_step_line(bill, today),
            self._action_line(bill, today),
        ]
        block = "\n".join(line for line in lines if line)
        ended = self._ended_line(bill, today)
        return "\n".join(part for part in (block, ended) if part)

    def _ended_line(self, bill: Bill, today: dt.date) -> str:
        """How the road ended. Without it a finished bill's card loses its last three lines and
        says nothing at all about being over (a card posted for a bill whose act was already in
        force, or one revived by `/republish`)."""
        if not is_over(bill, today=today) or next_phase(bill, today=today) is not None:
            return ""
        lb = self._labels
        act = bill.act
        if act is not None and act.entry_into_force is not None:
            when = self.fmt_date(act.entry_into_force)
            return f"{ICON['in_force']} <b>{esc(lb.already_in_force_since)}</b> {when}"
        if bill.discontinued_at is not None:
            carried = bill.summary.applicant_type is ApplicantType.CITIZENS
            label = lb.process_carried_over if carried else lb.process_discontinued
        elif any(st.stage_type == "ConstitutionalTribunalRuling" for st in bill.stages):
            # The listing leaves `passed` as it was, so without this the card of an act the
            # Tribunal struck down said nothing at all about being over.
            label = lb.process_tribunal_ruled
        elif veto_stood(bill.stages):
            # The API leaves `passed` true on a law the veto killed (art. 122 ust. 5 was never
            # reached), so the closure branch below never fires and the card said nothing at all
            # about being over — the word "вето" appeared only in the «Стадия» line.
            label = lb.process_veto_sustained
        elif bill.wykaz is not None:
            label = lb.wykaz_process_closed
        elif bill.rcl is not None:
            label = lb.rcl_process_closed
        elif bill.summary.closure_date is not None and bill.summary.passed is False:
            label = lb.process_not_enacted
        else:
            # `next_phase` gives up on a stage tree it does not recognise as well, and a bill the
            # Sejm passed is not one whose law failed: without the listing saying so, silence.
            return ""
        return f"{ICON['closed']} {esc(label)}"

    def _planned_adoption(self, bill: Bill, today: dt.date) -> str | None:
        """The quarter in which the register says the Council of Ministers means to adopt the
        bill. The field it comes from is free text and often carries the adoption note as well,
        so only the quarter is shown — and only while the quarter is still ahead, and the
        adoption with it: "Zrealizowany" means the Council has adopted the project, and the plan
        for it then read as a promise next to a path that had ticked that step off."""
        entry = bill.wykaz
        quarter = entry.planned_quarter if entry is not None else None
        if quarter is None or (entry is not None and entry.is_adopted):
            return None
        year, number = quarter
        if (year, number) < (today.year, (today.month - 1) // 3 + 1):
            return None
        return esc(self._labels.wykaz_planned.format(quarter=QUARTERS[number], year=year))

    def _path_line(self, bill: Bill, today: dt.date, *, road: Bill | None = None) -> str:
        """ "RCL ✓ → Сейм ✓ → комиссии ● → II и III чтение → Сенат → Президент → Dz.U. → в силе".

        The step is `bill`'s, the road is `road`'s: only a government bill starts on the wykaz
        and on RCL, and a reply under a jointly considered print's card shares the step but not
        the beginning of the road.
        """
        lb = self._labels
        road = road or bill
        phase = next_phase(bill, today=today)
        current: str | None
        if phase is None:
            act = bill.act
            if act is None or act.entry_into_force is None or act.entry_into_force > today:
                return ""  # over without an act (withdrawn, rejected, lapsed): see the closure line
            current = None  # in force: every step done
        elif phase.key.startswith("rcl_") and phase.key != "rcl_to_sejm":
            current = "rcl"
        else:
            current = PHASE_STEP.get(phase.key)
            if current is None:
                return ""
        steps = [s for s in PATH_STEPS if s not in GOVERNMENT_STEPS or government_path(road)]
        if current is not None and current not in steps:
            # The loop marks every step before `current` as done, so a step filtered out of the
            # line would mark the whole road done — "Dz.U. ✓ → в силе ✓" over a bill with no
            # text yet. Better no path line than a finished one.
            return ""
        parts: list[str] = []
        before = current is not None
        for step in steps:
            label = lb.path_steps[step]
            if step == current:
                parts.append(f"{label} ●")
                before = False
            elif before or current is None:
                parts.append(f"{label} ✓")
            else:
                parts.append(label)
        return f"{ICON['path']} <b>{esc(lb.path)}:</b> {esc(' → '.join(parts))}"

    def _step_template(
        self, key: str, *, named: bool, overdue: bool = False, dated: bool = True
    ) -> str | None:
        """The phase's wording, in the variant the moment calls for.

        `_overdue` once the constitutional term is out, because "(до 21 дня)" next to «срок истёк»
        in the same line says both things at once; `_unnamed` when there is no committee to name
        and `_undated` when the phase has no date, since `{committee}` and `{date}` left empty
        render a dangling dash and «консультации до , затем…».
        """
        labels = self._labels.next_step_labels
        if overdue and (late := labels.get(f"{key}_overdue")) is not None:
            return late
        if not dated and (open_ended := labels.get(f"{key}_undated")) is not None:
            return open_ended
        if not named and (plain := labels.get(f"{key}_unnamed")) is not None:
            return plain
        return labels.get(key)

    def _next_step_line(self, bill: Bill, today: dt.date) -> str:
        lb = self._labels
        phase = next_phase(bill, today=today)
        if phase is None:
            return ""
        urgent = is_urgent(bill)
        overdue = deadline_overdue(phase, today)
        # The urgent wording of art. 123 names the shortened term ("срочный режим: до 7 дней"),
        # which is the half of the line an expired term must not keep; the `_overdue` variant
        # names no term at all and suits both, with `urgent_mode` added below saying which it was.
        shortened = lb.urgent_step_labels.get(phase.key) if urgent and not overdue else None
        committees = self._committee_names(bill, phase.committees)
        template = shortened or self._step_template(
            phase.key, named=bool(committees), overdue=overdue, dated=phase.date is not None
        )
        if template is None:
            return ""
        text = template.format(
            date=self.fmt_date(phase.date) if phase.date else "",
            committee=committees,
        ).strip()
        if urgent and shortened is None:
            text = f"{text} ({lb.urgent_mode})"  # the shortened wordings name the mode themselves
        when = self._when(bill, phase, today, urgent=urgent)
        return f"{ICON['next']} <b>{esc(lb.next_step)}:</b> {esc(text)}{when}"

    def _when(self, bill: Bill, phase: Phase, today: dt.date, *, urgent: bool) -> str:
        """When the step is expected, in decreasing order of how much it is worth: a sitting
        already on the calendar, the statutory deadline, the quarter the government named for
        adopting the project, how long the bill has been waiting once it outlived the average,
        the average."""
        lb = self._labels
        upcoming = self._upcoming(bill, today, phase)
        if upcoming is not None:
            # The card's most-read line must not promise a day the committee itself announced
            # conditionally; the agenda post under it says what the condition is.
            mark = f" ({esc(lb.agenda_conditional_short)})" if upcoming.condition else ""
            return f" · {self._agenda_when(upcoming)}{mark}"
        if (deadline := phase.deadline) is not None:
            if not deadline_overdue(phase, today):
                return f" · {esc(lb.deadline_until)} {self.fmt_date(deadline)}"
            # What a passed term means is not the same for every step, and the number of days it
            # was is not the same for every bill: art. 123 halves the President's twenty-one.
            passed = (lb.urgent_deadline_passed_labels.get(phase.key) if urgent else None) or (
                lb.deadline_passed_labels.get(phase.key, lb.deadline_passed)
            )
            return f" · {esc(passed)}"
        if (planned := self._planned_adoption(bill, today)) is not None:
            # A step with a date of its own is never told how long it has been standing: the
            # government saying it means to adopt the project this quarter is the date a reader
            # plans around, and «принятие правительством: III кв. 2026 · без движения уже 9 мес.»
            # answers a question nobody asked with the one they did. Once the quarter has gone
            # `_planned_adoption` says nothing and the silence is the news again.
            return f" · {planned}"
        if (stalled := self._stalled_for(phase, today)) is not None:
            return f" · {esc(stalled)}"
        usual = (lb.urgent_durations.get(phase.key) if urgent else None) or (
            lb.typical_durations.get(phase.key)
        )
        return f" · {esc(usual)}" if usual else ""

    def _stalled_for(self, phase: Phase, today: dt.date) -> str | None:
        days = stalled_days(phase, today)
        if days is None:
            return None
        lb = self._labels
        if days < 2 * DAYS_PER_MONTH:
            return lb.stalled_for_weeks.format(weeks=days // 7)
        return lb.stalled_for_months.format(months=days // DAYS_PER_MONTH)

    def _translate_stage(self, stage: Stage) -> str | None:
        """The stage in the reader's language, or None when the labels have nothing for it: an
        RCL stage by the part of its name the labels know, a Sejm reading by its numeral, the
        rest by type. The one place a stage is translated (header, card, update bullets)."""
        lb = self._labels
        if stage.stage_type == RCL_STAGE_TYPE:
            name = stage.stage_name.lower()
            return next((t for part, t in lb.rcl_stage_labels.items() if part in name), None)
        if stage.stage_type == "SejmReading":
            match = _READING_NUMERAL.match(stage.stage_name)
            return lb.reading_stage.format(numeral=match.group(1)) if match else None
        if end_names_veto_sustained(stage):
            return lb.stage_veto_sustained
        return lb.stage_labels.get(stage.stage_type) or lb.stage_type_labels.get(stage.stage_type)

    def _current_stage(self, bill: Bill, last: Stage) -> str:
        """ "⛔ Президент наложил вето (28.08.2026) · направлен в комиссию ENM (03.09.2026)".

        The top-level stage first: it is what happened to the bill. Its newest child follows
        only when it adds something — the committee the bill went to, the vote — and never
        instead, which is what a flattened tree used to give (see `Bill.last_stage`).
        """
        parts = [self._dated_stage(bill, last)]
        detail = bill.last_stage_detail
        if detail is not None and self._translate_stage(detail) is not None:
            same_day = detail.date == last.date
            parts.append(self._dated_stage(bill, detail, dated=not same_day))
        return " · ".join(parts)

    def _dated_stage(self, bill: Bill, stage: Stage, *, dated: bool = True) -> str:
        when = f" ({self.fmt_date(stage.date)})" if stage.date and dated else ""
        return f"{self._stage_label(bill, stage)}{when}"

    def _stage_label(self, bill: Bill, stage: Stage) -> str:
        """The card's stage in the reader's language; Polish stays only where it names a body
        (a committee) or, for RCL, as the numbered original after the translation."""
        if stage.stage_type == "Referral" and stage.committee_code:
            committee = self._committee_display(bill, stage.committee_code, stage.committee_name)
            return f"{esc(self._labels.stage_labels['Referral'])} {esc(committee)}"
        label = self._translate_stage(stage)
        if label is None:
            return _quoted(stage.stage_name)
        if stage.stage_type == RCL_STAGE_TYPE:
            return f"{esc(label)} ({esc(stage.stage_name)})"
        return esc(label)

    def _action_line(
        self,
        bill: Bill,
        today: dt.date,
        *,
        agenda_item: AgendaItem | None = None,
        when_none: bool = True,
    ) -> str:
        """What a reader can do now, or (with `when_none`) why nothing. A post that exists to
        say something — the constitutional-deadline reminder, whose body lists the three things
        the President may do — reads worse for ending in «пока ничего»."""
        lb = self._labels
        if (
            bill.act is not None
            and bill.act.entry_into_force is not None
            and bill.act.entry_into_force <= today
        ):
            return f"{ICON['action']} <b>{esc(lb.action_now)}:</b> {esc(lb.action_in_force)}"
        actions: list[str] = []
        window = bill.consultation
        if bill.wykaz is not None:
            actions.extend(self._wykaz_actions(bill.wykaz))
        elif bill.rcl is not None:
            actions.extend(self._rcl_actions(bill, today))
        elif window is not None and window.is_open(today) and window.end is not None:
            page = window.survey_url or window.form_url
            where = (
                link(page, lb.action_consultation_page)
                if page
                else esc(lb.action_consultation_page)
            )
            actions.append(
                f"{esc(lb.action_send_opinion)} {where} {esc(lb.consultation_until)} "
                f"{self.fmt_date(window.end)}"
            )
        phase = next_phase(bill, today=today)
        if phase is not None and phase.key in COMMITTEE_PHASES:
            codes = phase.committees or ((agenda_item.committee_code,) if agenda_item else ())
            targets = [
                link(committee_web_url(bill.term, code), self._committee_display(bill, code, None))
                + f" ({link(committee_letter_url(bill.term, code), lb.link_committee_letter)})"
                for code in codes
                if code
            ]
            if targets:
                text = f"{esc(lb.action_committee)} {', '.join(targets)}"
                sitting = agenda_item if agenda_item and agenda_item.kind == "committee" else None
                sitting = sitting or self._upcoming(bill, today, phase, kind="committee")
                if sitting is not None and sitting.date > today:
                    text += f" {esc(lb.action_before_sitting)} {self.fmt_date(sitting.date)}"
                actions.append(text)
            hearing = open_hearing(bill, today)
            deadline = hearing_application_deadline(hearing) if hearing else None
            if hearing is not None and (deadline is None or deadline >= today):
                text = esc(lb.action_hearing)
                if deadline is not None:
                    text += f" {esc(lb.consultation_until)} {self.fmt_date(deadline)}"
                route = lb.action_hearing_route.format(email=HEARING_APPLICATION_EMAIL)
                rules = link(hearing_rules_url(bill.term), lb.link_hearing_rules)
                actions.append(f"{text}: {esc(route)} · {rules}")
        if phase is not None and phase.key == "senate":
            senate = self._senate_action(bill, today)
            if senate:
                actions.append(senate)
        if not actions:
            # Say so, and name the next window, rather than leave the reader guessing.
            nothing = self._nothing_to_do(phase) if when_none else None
            return (
                f"{ICON['action']} <b>{esc(lb.action_now)}:</b> {esc(nothing)}" if nothing else ""
            )
        return f"{ICON['action']} <b>{esc(lb.action_now)}:</b> " + "; ".join(actions)

    def _senate_action(self, bill: Bill, today: dt.date) -> str | None:
        """Where an opinion on the act goes in the Senate: its committees, their e-mail, the day
        they meet. Never art. 121's thirty days: the committees take the act within a week."""
        lb = self._labels
        act = bill.senate
        if act is None or not act.committees:
            return esc(lb.action_senate_pending)
        if act.committees_done(today):
            return None
        targets = [
            link(c.url, c.name) + (f" ({esc(c.email)})" if c.email else "") for c in act.committees
        ]
        verb = lb.action_senate if len(targets) == 1 else lb.action_senate_many
        text = f"{esc(verb)} {', '.join(targets)}"
        sitting = act.next_committee_sitting(today)
        if sitting is not None and sitting > today:
            text += f" {esc(lb.action_before_sitting)} {self.fmt_date(sitting)}"
        if act.print_number:
            text += f" {esc(lb.action_senate_in_polish.format(number=act.print_number))}"
        return f"{text} · {link(act.url, lb.link_senate_act)}"

    def _nothing_to_do(self, phase: Phase | None) -> str | None:
        """Why there is nothing to do, and what comes after it."""
        if phase is None:
            return None
        return self._labels.no_action_labels.get(phase.key)

    def _wykaz_actions(self, entry: WykazEntry) -> list[str]:
        """What a reader can do while a government plan has no published draft."""
        if not entry.is_open:
            return []
        lb = self._labels
        organ = ministry_name(entry.organ) if entry.organ else lb.wykaz_organ_unknown
        action = esc(lb.action_wykaz_interest.format(organ=organ))
        contacts = ministry_contact_url(entry.organ) if entry.organ else None
        if contacts is not None:
            action += f" · {link(contacts, lb.action_ministry_site)}"
        return [action]

    def _rcl_actions(self, bill: Bill, today: dt.date) -> list[str]:
        """What a reader of a government project can do, and with how much weight.

        The comment form and the zgłoszenie outlive the consultation — they stay open while the
        project is with the government — but offering them with an open window's emphasis is
        wrong, so once that window has shut they are offered as what is left, in a line saying so.
        """
        project = bill.rcl
        assert project is not None, "the action line routes only a bill with an RCL project here"
        lb = self._labels
        window = bill.consultation
        open_now = consultation_open(bill, today)
        actions: list[str] = []
        if window is not None and window.email and open_now:
            polish = lb.action_in_polish.format(wykaz=project.wykaz_number or project.number)
            text = esc(lb.action_email_ministry.format(email=window.email))
            if window.end is not None:
                text += f" {esc(lb.consultation_until)} {self.fmt_date(window.end)}"
            else:
                text += f" ({esc(lb.consultation_deadline_in_letter)})"
            actions.append(f"{text} {esc(polish)}")
        if not (project.is_open and not project.sent_to_sejm):
            return actions
        organ = project.applicant or lb.wykaz_organ_unknown
        interest = esc(lb.action_rcl_interest.format(organ=organ))
        contacts = ministry_contact_url(project.applicant) if project.applicant else None
        if contacts is not None:
            interest += f" · {link(contacts, lb.action_ministry_site)}"
        residual = [link(project.comment_url, lb.action_rcl_comment), interest]
        # A project that never had a public consultation (a quarter of them skip the stage) has
        # no window that shut, so there is nothing to say it did.
        if window is None or open_now:
            actions.extend(residual)
        else:
            actions.append(f"{esc(lb.action_rcl_window_closed)} " + "; ".join(residual))
        return actions

    def _upcoming(
        self, bill: Bill, today: dt.date, phase: Phase, *, kind: str | None = None
    ) -> AgendaItem | None:
        """The soonest future sitting naming the bill, in the venue the phase implies.

        A phase dates nothing unless a sitting of its own venue is scheduled: a committee's 08:30
        slot is not a third reading, and a phase outside both houses has no venue at all. The
        fallback to the next calendar item printed «подпись Президента · заседание Сейма № 65»
        over the constitutional deadline.
        """
        future = sorted((i for i in bill.agenda if i.date >= today), key=lambda i: i.date)
        if kind is not None:
            return next((i for i in future if i.kind == kind), None)
        preferred = []
        if phase.key in COMMITTEE_PHASES:
            preferred.append("committee")
        if phase.key in SITTING_PHASES:
            preferred.append("sejm")
        for wanted in preferred:
            item = next((i for i in future if i.kind == wanted), None)
            if item is not None:
                return item
        return None

    def _moved_line(self, was: AgendaItem, now: AgendaItem) -> str:
        """What the sitting moved from: the day when the day changed, otherwise the hour and the
        room it was announced with, which is what the reader wrote down."""
        lb = self._labels
        if was.date != now.date:
            return f"{esc(lb.sitting_moved_from)} {self.fmt_date(was.date)}"
        before = [was.start_time.strftime("%H:%M")] if was.start_time else []
        if was.room and was.room != now.room:
            before.append(was.room)
        return f"{esc(lb.sitting_moved_within_day)} {esc(' · '.join(before))}"

    def _sitting_notes(self, item: AgendaItem) -> list[str]:
        """What the committee's `notes` say about the sitting itself, above its agenda.

        The condition comes first because it decides whether there is a day to plan at all (21
        sittings of term 10 happen only if the Sejm refers something first). The application
        address is the rarest line the channel prints — 4 sittings in a term — and the only one
        that lets a reader into a hearing.
        """
        lb = self._labels
        lines: list[str] = []
        if item.condition is not None:
            what = lb.agenda_conditions.get(item.condition)
            condition = f"{lb.agenda_conditional} {what}." if what else f"{lb.agenda_conditional}…"
            lines.append(f"{ICON['note']} {esc(condition)}")
        if item.closed:
            lines.append(f"{ICON['note']} {esc(lb.agenda_closed_note)}")
        if item.apply_email:
            apply = f"{lb.agenda_apply} {item.apply_email}"
            if item.apply_by is not None:
                apply += f" · {lb.agenda_apply_by} {self.fmt_date(item.apply_by)}"
            lines.append(f"{ICON['action']} {esc(apply)}")
        return lines

    def _agenda_when(self, item: AgendaItem) -> str:
        lb = self._labels
        if item.kind == "committee":
            when = self.fmt_date(item.date)
            if item.start_time:
                when += f", {item.start_time.strftime('%H:%M')}"
            return esc(when)
        span = self.fmt_date(item.date)
        if item.end_date and item.end_date != item.date:
            # "15–18.09.2026" only holds when the month is shared and the format opens with the
            # day; across a month, or in a locale writing year first, both dates are spelled out.
            same_month = (item.date.year, item.date.month) == (
                item.end_date.year,
                item.end_date.month,
            )
            if same_month and lb.date_format.startswith("%d"):
                span = f"{item.date.day:02d}–{self.fmt_date(item.end_date)}"
            else:
                span = f"{self.fmt_date(item.date)} – {self.fmt_date(item.end_date)}"
        number = f"{lb.sejm_sitting} {item.sitting_number}, " if item.sitting_number else ""
        return esc(f"{number}{span}")

    def _committee_names(self, bill: Bill, codes: tuple[str, ...]) -> str:
        return ", ".join(self._committee_display(bill, code, None) for code in codes)

    def _committee_display(self, bill: Bill, code: str, name: str | None) -> str:
        """ "Komisja … (ASW)" when a stage or an agenda item knows the name, else the code."""
        if name is None:
            name = next(
                (
                    st.committee_name
                    for st in flatten_stages(bill.stages)
                    if st.committee_code == code and st.committee_name
                ),
                None,
            ) or next(
                (
                    i.committee_name
                    for i in bill.agenda
                    if i.committee_code == code and i.committee_name
                ),
                None,
            )
        return f"{name} ({code})" if name else code

    def _stage_line(self, stage: Stage, today: dt.date) -> str:
        """One bullet of the "new stages" list; may span two lines (voting + club breakdown)."""
        lb = self._labels
        when = f"{self.fmt_date(stage.date)}: " if stage.date else ""
        if stage.stage_type == "Voting" and stage.voting is not None:
            return when + self._voting_lines(stage.voting)
        if stage.stage_type == "SenatePosition" and stage.position:
            known = lb.senate_position_labels.get(stage.position.strip().lower())
            text = esc(known) if known else _quoted(stage.position)
            return when + text + self._print_suffix(stage)
        if stage.stage_type == "Referral" and stage.committee_code:
            name = stage.committee_code
            if stage.committee_name:
                name = f"{stage.committee_name} ({stage.committee_code})"
            return f"{when}{ICON['committee']} {esc(lb.referred_to_committee)}: {esc(name)}"
        if stage.stage_type == "CommitteeReport":
            label = lb.subcommittee_report if stage.sub_committee else lb.committee_report
            line = when + esc(label) + self._print_suffix(stage)
            known = _translate(stage.proposal, lb.proposal_labels)
            proposal = esc(known) if known else _quoted(stage.proposal)
            if proposal:
                line += f": {esc(lb.proposes)} {proposal}"
            return line
        if stage.stage_type == "PublicHearing":
            line = when + esc(lb.stage_type_labels["PublicHearing"])
            deadline = hearing_application_deadline(stage)
            if deadline is None:
                return line
            if deadline < today:
                return f"{line} — {esc(lb.hearing_applications_closed)}"
            return f"{line} — {esc(lb.hearing_apply_until)} {self.fmt_date(deadline)}"
        # An RCL stage's bullet keeps the numbered original: the header names it in the
        # reader's language already, and the number is what the RCL page shows.
        translated = None if stage.stage_type == RCL_STAGE_TYPE else self._translate_stage(stage)
        if translated is not None:
            line = when + esc(translated)
            known = _translate(stage.decision, lb.decision_labels)
            decision = esc(known) if known else _quoted(stage.decision)
            if decision:
                line += f" — {decision}"
            return line + self._print_suffix(stage)
        outcome = stage.decision or stage.position
        original = f"{stage.stage_name} — {outcome}" if outcome else stage.stage_name
        return when + _quoted(original) + self._print_suffix(stage)

    def _print_suffix(self, stage: Stage) -> str:
        """The print a stage produced, in brackets after the stage."""
        if not stage.print_number:
            return ""
        return f" ({esc(self._labels.print_number)} {esc(stage.print_number)})"

    def _voting_lines(self, v: VotingSummary) -> str:
        lb = self._labels
        totals = (
            f"{ICON['voting']} <b>{esc(lb.voting)}:</b> {v.yes} {esc(lb.votes_for)}, "
            f"{v.no} {esc(lb.votes_against)}, {v.abstain} {esc(lb.votes_abstain)}"
        )
        if v.not_participating:
            totals += f" · {esc(lb.not_voting)}: {v.not_participating}"
        if v.pdf_url:
            totals += f" · {link(v.pdf_url, lb.link_voting_pdf)}"
        if not v.clubs:
            return totals
        sides = [
            (lb.votes_for, [(c.club, c.yes) for c in v.clubs if c.yes]),
            (lb.votes_against, [(c.club, c.no) for c in v.clubs if c.no]),
            (lb.votes_abstain, [(c.club, c.abstain) for c in v.clubs if c.abstain]),
        ]
        rendered = []
        for label, entries in sides:
            if not entries:
                continue
            entries.sort(key=lambda e: -e[1])
            shown = ", ".join(f"{esc(club)} {n}" for club, n in entries[:CLUBS_PER_SIDE])
            if len(entries) > CLUBS_PER_SIDE:
                shown += ", …"
            rendered.append(f"{esc(label.capitalize())}: {shown}")
        return totals + "\n  " + " · ".join(rendered)

    @staticmethod
    def _assemble(
        head: Sequence[str], *, flexible: Sequence[str] = (), tail: Sequence[str] = ()
    ) -> str:
        """Join blocks with blank lines: `head`, then the `flexible` blocks, then `tail`; empty
        blocks are dropped. The flexible blocks are shrunk (in order) until the message is under
        the limit; the head and tail blocks (header, facts, links, tags) are never cut, except
        as a last resort when they alone exceed the limit: then whole lines go from the end."""
        kept_head = [b for b in head if b]
        kept_tail = [b for b in tail if b]
        pending = [b for b in flexible if b]
        budget = MESSAGE_LIMIT - sum(length(b) + 2 for b in kept_head + kept_tail)
        shrunk: list[str] = []
        for block in pending:
            allowed = max(0, budget - 2 * (len(pending) - len(shrunk)))
            if length(block) > allowed:
                block = shrink_block(block, allowed) if allowed > 40 else ""
            if block:
                shrunk.append(block)
                budget -= length(block) + 2
        text = "\n\n".join(kept_head + shrunk + kept_tail)
        return text if length(text) <= MESSAGE_LIMIT else _cut_lines(text, MESSAGE_LIMIT)


def _quoted(value: str | None) -> str:
    """A value the labels have no wording for, in italics: the reader sees what the Sejm wrote
    rather than a Polish phrase standing where a Russian one belongs."""
    return f"<i>{esc(value)}</i>" if value else ""


def _translate(value: str | None, labels: dict[str, str]) -> str | None:
    """The label of the first fragment found in `value` (lower case); None when nothing matches."""
    if not value:
        return None
    lowered = value.lower()
    return next((label for part, label in labels.items() if part in lowered), None)


def _command_cost(outcome: CommandOutcome) -> str:
    """`⏱ run 11.09.2026 17:07 UTC · 41.2s · tokens 95.3k/1.1k · ≈ $0.48`: when the run that
    answered the command started, how long the command took and what the model cost. The token
    part is dropped when the model was not called (`/show`, a stored verdict)."""
    parts: list[str] = []
    if outcome.run_started_at is not None:
        parts.append(f"run {outcome.run_started_at.strftime('%d.%m.%Y %H:%M')} UTC")
    if outcome.seconds is not None:
        parts.append(f"{outcome.seconds:.1f}s")
    if outcome.usage:
        spent_in = sum(u.input + u.cache_read for u in outcome.usage.values())
        spent_out = sum(u.output for u in outcome.usage.values())
        parts.append(f"tokens {format_tokens(spent_in)}/{format_tokens(spent_out)}")
        parts += [
            f"{esc(model.removeprefix('claude-'))} {format_tokens(u.input + u.cache_read)}"
            for model, u in outcome.usage.items()
            if len(outcome.usage) > 1
        ]
        cost = cost_usd(outcome.usage)
        if cost is not None:
            parts.append(f"≈ {format_usd(cost)}")
    return f"⏱ {' · '.join(parts)}" if parts else ""


def _counts(counts: dict[str, int]) -> str:
    """`analysis_pending 3 · analyzed 43`, the busiest first; "none" for an empty database."""
    if not counts:
        return "none"
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return " · ".join(f"{esc(name)} {value}" for name, value in ordered)


def _run_line(report: RunReport) -> str:
    """One row of `/runs`: the columns `lexinform runs` prints, as one line of a message.

    A run recorded before the per-model breakdown existed carries tokens but no usage: its cost
    is unknown, not zero, and `format_usd` says so.
    """
    cost = cost_usd(report.llm_usage) if report.llm_usage or not report.llm_input_tokens else None
    counters = (
        f"{report.discovered}/{report.analyzed}/{report.published}/{report.updates}"
        " disc·anal·publ·upd"
    )
    errors = f" · {len(report.errors)} error(s)" if report.errors else ""
    verdict = "ok" if report.ok else "ERR"
    return (
        f"• <b>{report.started_at:%d.%m %H:%M}</b> {esc(report.mode)} {verdict}"
        f" · {counters} · {format_tokens(report.llm_input_tokens)}"
        f"/{format_tokens(report.llm_output_tokens)} · {format_usd(cost)}{errors}"
    )


def _orphan_line(bill: Bill) -> str:
    """A stranded re-analysis goes back to `analyzed`: `analysis_pending` pays a first analysis."""
    status = BillStatus.ANALYZED if bill.analysis is not None else BillStatus.ANALYSIS_PENDING
    return f"<b>{esc(bill.number)}</b> → /reset {esc(bill.number)} to={status.value}"


def _runs_line(snapshot: StatusSnapshot) -> str:
    """What the recorded runs of the window did and cost, and when the last one started —
    the answer to "is it still running at all", which no single run report gives."""
    runs = snapshot.runs
    if not runs:
        return f"🏃 <b>runs</b>: none in {snapshot.days} days"
    usage: dict[str, TokenUsage] = {}
    for report in runs:
        merge_usage(usage, report.llm_usage)
    cost = cost_usd(usage)
    parts = [
        f"{len(runs)} in {snapshot.days} days",
        f"{sum(r.published for r in runs)} card(s)",
        f"{sum(r.updates for r in runs)} update(s)",
    ]
    if failed := sum(1 for r in runs if not r.ok):
        parts.append(f"{failed} with errors")
    if cost is not None and usage:
        parts.append(f"≈ {format_usd(cost)}")
    parts.append(f"last {runs[0].started_at:%d.%m %H:%M} UTC")
    return "🏃 <b>runs</b>: " + " · ".join(parts)


def _tokens_line(report: RunReport) -> str:
    """`Total $0.33 · opus-5 $0.32 · sonnet-5 $0.009` then, on its own line, `tokens in/out:
    12345/678 · cache read 4.0k` — the total leads because it is the one figure read at a
    glance; empty when the model was never called, `$?` when its price is unknown."""
    if not (report.llm_input_tokens or report.llm_output_tokens or report.llm_usage):
        return ""
    lines = []
    cost = cost_usd(report.llm_usage)
    if report.llm_usage:
        money = [f"Total {format_usd(cost)}"]
        money += [
            f"{esc(model.removeprefix('claude-'))} {format_usd(cost_usd({model: u}))}"
            for model, u in report.llm_usage.items()
        ]
        lines.append(" · ".join(money))
    tokens = [f"tokens in/out: {report.llm_input_tokens}/{report.llm_output_tokens}"]
    cached = sum(u.cache_read + u.batch_cache_read for u in report.llm_usage.values())
    written = sum(u.cache_creation + u.batch_cache_creation for u in report.llm_usage.values())
    if cached or written:
        tokens.append(f"cache read {format_tokens(cached)}")
        tokens.append(f"cache write {format_tokens(written)}")
    lines.append(" · ".join(tokens))
    return "\n".join(lines)


def _spenders_line(report: RunReport, *, top: int = 3) -> str:
    """`spent on: 2699 reanalysis $1.58 · 3039 analysis $0.21`, the costliest calls of the run.

    One tokens figure for a whole run cannot be accounted for afterwards — the run of 13 Sept
    2026 billed 316,767 input tokens with nothing to say which call made them — and the phases
    do not spend evenly. Shown only where a call cost a cent or more, so a quiet run stays quiet.
    """
    priced = [(cost, call) for call in report.llm_calls if (cost := cost_usd(call.usage))]
    priced.sort(key=lambda pair: -pair[0])
    named = [
        f"{esc(call.number)} {call.kind}{' batch' if call.batched else ''} {format_usd(cost)}"
        for cost, call in priced[:top]
        if cost >= 0.01
    ]
    return "spent on: " + " · ".join(named) if named else ""


def _section(icon: str, title: str, *lines: str, empty: str = "") -> str:
    """`🔎 <b>title</b>` followed by the non-empty lines, one per row; `empty` is the row shown
    when there are none."""
    rows = [line for line in lines if line] or ([empty] if empty else [])
    return "\n".join([f"{icon} <b>{esc(title)}</b>", *rows])


def _counters(*items: tuple[str, int]) -> str:
    """`Sejm: 3 new · prefilter hits: 1`: the templates whose counter is not zero, filled in and
    joined; empty when every counter is zero."""
    return " · ".join(template.format(value) for template, value in items if value)


def _number_ref(number: str) -> str:
    """`druk 2695` / `RCL/12414402` / `RPW/29075/2026` / `WPL/UD368`: how a bill is named.

    Only a Sejm print is a druk; the register's own rows were being called one too."""
    plain = is_rcl_number(number) or is_pre_print_number(number) or is_wykaz_number(number)
    return esc(number) if plain else f"druk {esc(number)}"


def _verdict_ref(verdict: AnalysisVerdict) -> str:
    """The same, linked to the process page when the term is known (reports stored before the
    field existed have none)."""
    label = _number_ref(verdict.number)
    if verdict.term is None:
        return label
    return link(process_web_url(verdict.term, verdict.number), label)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _bullet(text: str) -> str:
    """One row of a report section: clipped, because an error or a note quotes an outside system."""
    return f"• {esc(_clip(text, QUOTED_LINE_CHARS))}"


def _tag_safe(number: str) -> str:
    return "".join(ch for ch in number if ch.isalnum() or ch == "_")


def _number_tag(term: int, number: str, wykaz_number: str | None) -> str:
    """`#UC104` (`#RCL_12414402` when the wykaz number is unknown), `#RPW_29075_2026`,
    `#kadencja10druk3039`.

    A government project is tagged with its number in the wykaz prac RM: that number is the
    project's identity from the plan through RCL to the druk, so one search finds the whole
    thread. It is not the RCL id, which is why the tag does not say `RCL`; only a project the
    register does not number is tagged by the id of its page."""
    if is_wykaz_number(number):
        return "#" + _tag_safe(wykaz_entry_number(number))
    if is_rcl_number(number):
        if wykaz_number:
            return "#" + _tag_safe(wykaz_number)
        return "#RCL_" + _tag_safe(number.removeprefix(RCL_PREFIX))
    if is_pre_print_number(number):
        return "#" + _tag_safe(number.replace("/", "_"))
    return f"#kadencja{term}druk{_tag_safe(number)}"


def _cut_lines(text: str, limit: int) -> str:
    """Drop whole lines from the end until `text` fits `limit` (every line of a message is
    HTML-balanced on its own, so no tag is split). Better a card without its last lines than
    Telegram's 400 and a lost post; a message without a single fitting line is left alone."""
    lines = text.split("\n")
    while len(lines) > 1:
        lines.pop()
        kept = "\n".join(lines).rstrip()
        if kept and length(kept) <= limit:
            return kept
    return text


def shrink_block(block: str, allowed: int) -> str:
    """Trim a block to `allowed` chars keeping its HTML well-formed.

    Blocks are "<b>header</b>\n<body>"; cutting is only allowed inside the body, so the header is
    never split. A body carrying markup of its own is cut between lines and nowhere else, so no
    tag or entity is ever split. A block that cannot keep its header is dropped.
    """
    if block.endswith("</pre>"):
        opening = block.index("<pre>") + len("<pre>")
        inner = block[opening : -len("</pre>")]
        room = allowed - length(block[:opening]) - len("</pre>")
        return block[:opening] + fit(inner, max(room, 0)) + "</pre>" if room > 10 else ""
    header_end = block.find("\n")
    if header_end == -1 or length(block[: header_end + 1]) >= allowed:
        return ""
    head, body = block[: header_end + 1], block[header_end + 1 :]
    trim = fit_lines if "<" in body else fit
    kept = trim(body, allowed - length(head))
    return head + kept if kept.strip(ELLIPSIS) else ""
