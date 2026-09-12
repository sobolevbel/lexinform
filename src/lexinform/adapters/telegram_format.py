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
    GOVERNMENT_STEPS,
    PATH_STEPS,
    PHASE_STEP,
    RCL_PREFIX,
    RCL_STAGE_TYPE,
    SITTING_PHASES,
    WYKAZ_REGISTER_URL,
    ActInfo,
    AgendaItem,
    AnalysisVerdict,
    ApplicantType,
    Bill,
    CommandOutcome,
    ConsultationWindow,
    IncomingCommand,
    OutcomeStatus,
    Phase,
    PrintInfo,
    RclProject,
    RunReport,
    Stage,
    StatusChange,
    VotingSummary,
    WykazEntry,
    about_ukraine,
    committee_web_url,
    consultation_open,
    event_keys,
    flatten_stages,
    government_path,
    hearing_application_deadline,
    is_pre_print_number,
    is_rcl_number,
    is_wykaz_number,
    next_phase,
    open_hearing,
    process_web_url,
    reaches_sejm,
    told_stages,
    update_event,
    wykaz_entry_number,
)
from lexinform.pricing import cost_usd

MESSAGE_LIMIT = 4096
ELLIPSIS = "…"
QUARTERS = {1: "I", 2: "II", 3: "III", 4: "IV"}

# What the technical channel accepts (English, like the run report; the operator's language).
COMMAND_HELP = (
    "<b>commands</b> (a bill is a druk number, RPW/…, RCL/…, UC164, RM-… or a link to"
    " sejm.gov.pl / api.sejm.gov.pl / legislacja.rcl.gov.pl):\n"
    "• <code>/analyze BILL</code> — fetch, prefilter, analyse; post the card when relevant"
    " and important enough, then follow it\n"
    "• <code>/analyze BILL force</code> — analyse past the prefilter, a previous analysis"
    " and the cost guard\n"
    "• <code>/analyze BILL publish</code> — post a relevant card even below the score threshold\n"
    "• <code>/show BILL</code> — what the database knows\n"
    "• <code>/skip BILL</code> — silence a false positive (no analysis, no card)\n"
    "• <code>/republish BILL</code> — post the card again\n"
    "• <code>/help</code>"
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
    "links": "🔗",
    "new_stages": "🧭",
    "path": "🗺",
    "changed": "🆕",
    "passed": "✅",
    "closed": "🏁",
    "note": "ℹ️",
    "joint": "🔀",
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
    "senate": "🏛",
    "senate_no_amendments": "✅",
    "senate_amendments": "📋",
    "senate_rejected": "❌",
    "senate_considered": "🏛",
    "to_president": "🏛",
    "signed": "✍️",
    "veto": "⛔",
    "tribunal": "⚖️",
    "text_changed": "🆕",
    "withdrawn": "🏁",
    "discontinued": "🏁",
    "rcl_to_sejm": "🔢",
    "rcl_closed": "🏁",
    "rcl_started": "📄",
    "wykaz_withdrawn": "🚫",
}
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_READING_NUMERAL = re.compile(r"^\s*(I{1,3})\s+czytanie", re.IGNORECASE)
CLUBS_PER_SIDE = 4


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


def lead(text: str) -> str:
    """The first sentence: what an update repeats of the summary the card already carries."""
    first = _SENTENCE_END.split(text.strip(), maxsplit=1)[0]
    return first.strip()


def fit(text: str, limit: int) -> str:
    """Trim to `limit` characters at a paragraph/line boundary, appending an ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
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
        self, language: str = "ru", *, today: Callable[[], dt.date] = dt.date.today
    ) -> None:
        """`today` dates "what comes next", the countdowns and the consultation tag when a
        message method is not given the day explicitly: production passes the run's clock in
        Warsaw time, so a post never depends on the machine's zone."""
        self._labels: Labels = labels_for(language)
        self._today = today

    def new_bill(
        self, bill: Bill, print_info: PrintInfo | None, *, today: dt.date | None = None
    ) -> RenderedMessage:
        """The card. `today` decides whether the public consultation still counts as open."""
        if bill.analysis is None:
            raise ValueError(f"bill {bill.number} has no analysis")
        a = bill.analysis.analysis
        lb = self._labels
        today = today or self._today()

        header = self._header(ICON["new_bill"], self._card_header(bill), bill)
        meta = (
            f"{score_icon(a.score)} <b>{esc(lb.importance)}:</b> {importance_bar(a.score)} "
            f"{a.score}/5 — {esc(lb.score_labels.get(a.score, ''))}\n"
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

    def _card_header(self, bill: Bill) -> str:
        lb = self._labels
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
        assert bill.analysis is not None
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
        last = bill.last_stage
        if last is not None:
            when = f" ({self.fmt_date(last.date)})" if last.date else ""
            stage = f"{self._stage_label(bill, last)}{when}"
            meta_lines.append(self._field(ICON["stage"], lb.stage, stage))
        elif bill.wykaz is not None:
            meta_lines.append(self._field(ICON["stage"], lb.stage, esc(lb.wykaz_stage)))
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
        links = [link(s.web_url, lb.link_process)]
        pdf = print_info.main_pdf if print_info else None
        if pdf is not None:
            links.append(link(pdf.url, lb.link_pdf))
        if s.rcl_link:
            links.append(link(s.rcl_link, lb.link_rcl))
        return links

    def _card_tags(self, bill: Bill, today: dt.date) -> str:
        """Each tag answers one search: this bill's whole thread, by importance, by topic, where
        an opinion can still be sent, about citizens of Ukraine, government projects before the
        Sejm, and the bills of the term."""
        assert bill.analysis is not None
        a = bill.analysis.analysis
        lb = self._labels
        return " ".join(
            [
                self._thread_tags(bill),
                f"#{lb.tag_importance}{a.score}",
                f"#{lb.category_tags.get(a.category, a.category)}",
                *([f"#{lb.tag_consultations}"] if consultation_open(bill, today) else []),
                *([f"#{lb.tag_ukraine}"] if about_ukraine(bill) else []),
                *([f"#{lb.tag_rcl}"] if bill.rcl is not None else []),
                *([f"#{lb.tag_wykaz}"] if bill.wykaz is not None else []),
                self._term_tag(bill.summary.term),
            ]
        )

    def joint_bill(
        self, bill: Bill, primary: Bill, print_info: PrintInfo | None
    ) -> RenderedMessage:
        """Reply under `primary`'s card: `bill` is considered jointly with it and gets no card of
        its own. Title, who submitted it and when, links; the analysis stays the card's, which
        is re-done when the committee's joint text appears."""
        lb = self._labels
        s = bill.summary
        header = self._header(ICON["joint"], lb.joint_bill_header, bill)
        others = [primary.number, *(n for n in s.prints_considered_jointly if n != primary.number)]
        note = f"{ICON['note']} {esc(lb.joint_bill_note.format(numbers=', '.join(others)))}"
        facts = f"{note}\n{self._applicant_line(bill)}"
        links_block = self._links(self._card_links(bill, print_info))
        # Its own tag and the thread's: a search for either finds the reply.
        tags = f"{self._number_tag(bill)} {self._thread_tags(primary)}"
        return RenderedMessage(text=self._assemble([header, facts, links_block, tags]))

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

        stage_lines = [f"• {self._stage_line(st)}" for st in told_stages(change.new_stages)]
        stages_block = (
            f"{ICON['new_stages']} <b>{esc(lb.new_stages)}</b>\n" + "\n".join(stage_lines)
            if stage_lines
            else ""
        )
        closure = self._closure_line(bill, change, event)
        consultation = self._consultation_line(bill, today)
        over = change.withdrawn or change.discontinued
        steps = "" if over else self._steps_block(bill, today)

        summary_block = ""
        changes_block = ""
        amendments_block = self._amendments_block(change)
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

        # Event tags only when the reply carries the event a reader would search for.
        tags = " ".join(
            [f"#{lb.event_tags[key]}" for key in event_keys(change) if key in lb.event_tags]
            + [self._thread_tags(bill)]
        )

        text = self._assemble(
            [header, badge],
            flexible=[stages_block, amendments_block, changes_block, summary_block],
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
            closure = f"{ICON['closed']} {esc(lb.process_withdrawn)}"
        elif change.closure_detected and bill.wykaz is not None:
            closure = f"{ICON['closed']} {esc(lb.wykaz_process_closed)}"
        elif change.closure_detected and bill.rcl is not None:
            closure = f"{ICON['closed']} {esc(lb.rcl_process_closed)}"
        elif change.closure_detected and event not in (
            "passed",
            "rejected",
            "rcl_closed",
            "wykaz_withdrawn",
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
        """The process (or RCL project) page, the text the update is about, the amendments."""
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
        return links

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
        links_block = self._links(self._act_links(bill, act))
        tags = self._tag_line(lb.tag_published, bill)
        return RenderedMessage(text=self._assemble([header, facts, links_block, tags]))

    def in_force(self, bill: Bill) -> RenderedMessage:
        lb = self._labels
        act = bill.act
        if act is None or act.entry_into_force is None:
            raise ValueError(f"bill {bill.number} has no entry-into-force date")
        header = self._header(ICON["in_force"], lb.in_force_header, bill, act.title)
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
        tags = self._tag_line(lb.tag_in_force, bill)
        text = self._assemble(
            [header, facts], flexible=[summary_block, practical], tail=[links_block, tags]
        )
        return RenderedMessage(text=text)

    def consultation_deadline(self, bill: Bill, *, today: dt.date) -> RenderedMessage:
        """Reply under the card a few days before the public consultation closes."""
        lb = self._labels
        window = bill.consultation
        if window is None or window.end is None:
            raise ValueError(f"bill {bill.number} has no consultation end date")
        header = self._header(ICON["consultation"], lb.consultation_deadline_header, bill)
        where = self._consultation_where(bill, window, sejm_label=lb.consultation_hint)
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
        tags = self._tag_line(lb.tag_consultations, bill)
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
            period = self._consultation_period(window)
            facts = f"{self._field(ICON['effective'], lb.consultation, period)}\n{facts}"
        steps = self._steps_block(bill, today or self._today())
        links_block = self._links(self._consultation_links(bill, window))
        tags = self._tag_line(lb.tag_consultations, bill)
        return RenderedMessage(text=self._assemble([header, facts, steps, links_block, tags]))

    def agenda(
        self, bill: Bill, item: AgendaItem, *, today: dt.date | None = None
    ) -> RenderedMessage:
        """Reply under the card: the bill is on the agenda of a committee or Sejm sitting."""
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
        tags = self._tag_line(
            lb.tag_committee_sitting if is_committee else lb.tag_sejm_sitting, bill
        )
        summary_block = ""
        if bill.analysis is not None:
            summary_block = self._summary_reminder(bill.analysis.analysis.summary, full=False)
        text = self._assemble(
            [header, facts], flexible=[summary_block], tail=[action, links_block, tags]
        )
        return RenderedMessage(text=text)

    def hearing_deadline(self, bill: Bill, hearing: Stage, *, today: dt.date) -> RenderedMessage:
        """Reply under the card a few days before applications to a public hearing close."""
        lb = self._labels
        deadline = hearing_application_deadline(hearing)
        if deadline is None or hearing.date is None:
            raise ValueError(f"bill {bill.number}: the hearing has no date")
        header = self._header(ICON["hearing"], lb.hearing_deadline_header, bill)
        facts = (
            f"{ICON['effective']} <b>{esc(lb.hearing_on)}</b> {self.fmt_date(hearing.date)} · "
            f"{esc(lb.hearing_apply_until)} <b>{self.fmt_date(deadline)}</b> · "
            f"{self._countdown((deadline - today).days)}\n"
            f"{ICON['action']} {esc(lb.hearing_hint)}"
        )
        links = [link(bill.summary.web_url, lb.link_process)]
        phase = next_phase(bill, today=today)
        for code in phase.committees if phase else ():
            links.append(link(committee_web_url(bill.term, code), lb.link_committee))
        links_block = self._links(links)
        tags = self._tag_line(lb.tag_hearing, bill)
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

    def run_report(self, report: RunReport, log_lines: list[str]) -> RenderedMessage:
        lb = self._labels
        status = "✅" if report.ok else "❌"
        duration = report.duration_seconds
        head = (
            f"<b>{status} {esc(lb.run_report_title)}</b>\n"
            f"mode: {esc(report.mode)} · since: {esc(report.since.strftime('%Y-%m-%d %H:%M'))}"
            + (f" · term {report.term}" if report.term is not None else "")
            + (f" · {duration}s" if duration is not None else "")
        )
        # Zero counters say nothing: a section lists what happened, or that nothing did.
        sections = [
            _section(
                "🔎",
                "discovery",
                _counters(
                    ("Sejm: {} new", report.discovered),
                    ("+{} without print number", report.pre_print_discovered),
                    ("prefilter hits: {}", report.prefilter_hits),
                ),
                _counters(
                    ("RCL: {} new", report.rcl_discovered),
                    ("prefilter hits: {}", report.rcl_prefilter_hits),
                ),
                _counters(
                    ("wykaz prac RM: {} new", report.wykaz_discovered),
                    ("{} older entries match, not followed", report.wykaz_backlog),
                ),
                _counters(
                    ("{} already over when first seen, not followed", report.over_on_arrival),
                ),
                _counters(
                    ("text prefilter: checked {}", report.text_prefilter_checked),
                    ("hits {}", report.text_prefilter_hits),
                    ("unreadable {}", report.text_prefilter_unreadable),
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
                ),
                _tokens_line(report),
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
                ),
                _counters(
                    ("acts: {}", report.acts_published),
                    ("in force: {}", report.in_force_posted),
                    ("consultation reminders: {}", report.consultation_reminders),
                    ("results: {}", report.consultation_results_posted),
                    ("agenda: {}", report.agenda_posted),
                    ("hearings: {}", report.hearing_reminders),
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
            sections.append(_section("🛠", "commands", *(f"• {esc(c)}" for c in report.commands)))
        if report.errors:
            sections.append(_section("❌", "errors", *(f"• {esc(e)}" for e in report.errors)))
        if report.notes:
            sections.append(_section("ℹ️", "notes", *(f"• {esc(n)}" for n in report.notes)))
        rejected = ""
        if report.rejected:
            rejected = _section(
                "🗂",
                "analysed, not published",
                *(
                    f"• {_verdict_ref(v)} · {esc(v.reason)} · {esc(_clip(v.title, 110))}"
                    for v in report.rejected
                ),
            )
        logs = ""
        if log_lines:
            logs = "⚠️ <b>warnings</b>\n<pre>" + esc("\n".join(log_lines)) + "</pre>"
        text = self._assemble([head, "\n\n".join(sections)], flexible=[rejected, logs])
        return RenderedMessage(text=text)

    def command_reply(self, command: IncomingCommand, outcome: CommandOutcome) -> RenderedMessage:
        """The answer to an operator command, English like the run report: what the bot knows
        about the bill, the verdict, and what happened to the card."""
        icon = {
            OutcomeStatus.ANALYSED: "🤖",
            OutcomeStatus.SKIPPED: "⏭",
            OutcomeStatus.SHOWN: "🔎",
            OutcomeStatus.SILENCED: "🔇",
            OutcomeStatus.REPUBLISHED: "📣",
            OutcomeStatus.HELP: "🛠",
            OutcomeStatus.EXECUTED_EARLIER: "🕗",
        }.get(outcome.status, "❌")
        head = f"{icon} <b>{esc(outcome.status)}</b> · <code>{esc(command.text)}</code>"
        if outcome.status is OutcomeStatus.HELP:
            body = [esc(outcome.note), COMMAND_HELP] if outcome.note else [COMMAND_HELP]
            return RenderedMessage(text="\n\n".join([head, *body]))
        blocks = [head]
        bill = outcome.bill
        if bill is not None:
            blocks.append(self._bill_facts(bill, full=outcome.status is OutcomeStatus.SHOWN))
        if outcome.message_id is not None:
            blocks.append(f"📣 card posted: message {outcome.message_id}")
        if outcome.note:
            blocks.append(esc(outcome.note))
        blocks.append(_command_cost(outcome))
        return RenderedMessage(text=self._assemble(blocks))

    def _bill_facts(self, bill: Bill, *, full: bool) -> str:
        """The bill's title and link, its verdict, and (for /show) its status and last stage."""
        lines = [
            f"<b>{self._number_label(bill)}</b> · "
            + link(process_web_url(bill.term, bill.number), esc(_clip(bill.summary.title, 160)))
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

    def _tag_line(self, tag: str, bill: Bill) -> str:
        """The message kind's tag and the thread's."""
        return f"#{tag} {self._thread_tags(bill)}"

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

    def _summary_reminder(self, summary: str, *, full: bool) -> str:
        """`📝 Суть проекта: <first sentence>` or, after a re-analysis, the whole summary."""
        lb = self._labels
        text = summary.strip()
        if not text:
            return ""
        if full:
            return f"{ICON['about']} <b>{esc(lb.current_summary)}</b>\n{esc(text)}"
        return f"{ICON['about']} <b>{esc(lb.current_summary)}:</b> {esc(lead(text))}"

    def _number_label(self, bill: Bill) -> str:
        if bill.wykaz is not None:
            return esc(bill.wykaz.number)
        if bill.rcl is not None:
            return esc(bill.rcl.wykaz_number or bill.number)
        if bill.is_pre_print:
            return f"{esc(bill.number)} ({esc(self._labels.no_print_yet)})"
        return f"druk nr {esc(bill.number)}"

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
        return " ".join(tags)

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
                who += f" — {esc(entry.organ)}"
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
            links.append(link(project.wykaz_url, lb.link_wykaz))
        return links

    def _consultation_line(self, bill: Bill, today: dt.date) -> str:
        window = bill.consultation
        if window is None:
            return ""
        lb = self._labels
        if window.end is not None and not window.is_open(today):
            # A date range, like a bare "until <date>", reads as an invitation; a consultation
            # that is over says so and drops the ways to send an opinion (the letter stays: it
            # names the ministry and its e-mail). The end date is what the reader needs, so the
            # range collapses to it.
            closed = f"{esc(lb.consultation_closed_on)} {self.fmt_date(window.end)}"
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
            f"{self._consultation_where(bill, window)}"
        )

    def _rcl_deadline(self, bill: Bill, window: ConsultationWindow) -> str:
        """ "до 08.09.2026 (7 дн. с даты письма)"."""
        lb = self._labels
        assert window.end is not None
        text = f"{esc(lb.consultation_until)} {self.fmt_date(window.end)}"
        days = bill.rcl.consultation.days if bill.rcl and bill.rcl.consultation else None
        if days is not None:
            text += f" ({esc(lb.consultation_days_from_letter.format(days=days))})"
        return text

    def _consultation_where(
        self, bill: Bill, window: ConsultationWindow, *, sejm_label: str | None = None
    ) -> str:
        """Where an opinion goes: the Sejm form, or the ministry's e-mail and the letter (RCL)."""
        lb = self._labels
        if window.source == "sejm":
            form = window.form_url
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
            links.insert(0, link(window.form_url, lb.consultation_link))
        return links

    def _consultation_period(self, window: ConsultationWindow) -> str:
        lb = self._labels
        assert window.end is not None
        if window.start:
            return f"{self.fmt_date(window.start)} — {self.fmt_date(window.end)}"
        return f"{esc(lb.consultation_until)} {self.fmt_date(window.end)}"

    def _steps_block(self, bill: Bill, today: dt.date) -> str:
        """Where the bill is on its path, "what comes next" (dated when a sitting is scheduled,
        else with the usual duration) and "what you can do" (or why nothing, for now)."""
        lines = [
            self._path_line(bill, today),
            self._next_step_line(bill, today),
            self._action_line(bill, today),
        ]
        return "\n".join(line for line in lines if line)

    def _planned_adoption(self, bill: Bill) -> str | None:
        """The quarter in which the register says the Council of Ministers means to adopt the
        bill. The field it comes from is free text and often carries the adoption note as well,
        so only the quarter is shown."""
        entry = bill.wykaz
        quarter = entry.planned_quarter if entry is not None else None
        if quarter is None:
            return None
        year, number = quarter
        return esc(self._labels.wykaz_planned.format(quarter=QUARTERS[number], year=year))

    def _path_line(self, bill: Bill, today: dt.date) -> str:
        """ "RCL ✓ → Сейм ✓ → комиссии ● → II и III чтение → Сенат → Президент → Dz.U. → в силе"."""
        lb = self._labels
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
        steps = [s for s in PATH_STEPS if s not in GOVERNMENT_STEPS or government_path(bill)]
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

    def _next_step_line(self, bill: Bill, today: dt.date) -> str:
        lb = self._labels
        phase = next_phase(bill, today=today)
        if phase is None:
            return ""
        template = lb.next_step_labels.get(phase.key)
        if template is None:
            return ""
        text = template.format(
            date=self.fmt_date(phase.date) if phase.date else "",
            committee=self._committee_names(bill, phase.committees),
        ).strip()
        upcoming = self._upcoming(bill, today, phase)
        if upcoming is not None:
            suffix = f" · {self._agenda_when(upcoming)}"
        elif phase.deadline is not None:
            suffix = f" · {esc(lb.deadline_until)} {self.fmt_date(phase.deadline)}"
        elif (planned := self._planned_adoption(bill)) is not None:
            suffix = f" · {planned}"
        else:
            usual = lb.typical_durations.get(phase.key)
            suffix = f" · {esc(usual)}" if usual else ""
        return f"{ICON['next']} <b>{esc(lb.next_step)}:</b> {esc(text)}{suffix}"

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
            if match:
                return lb.next_step_labels["second_reading"].replace("II", match.group(1))
            return None
        return lb.stage_labels.get(stage.stage_type) or lb.stage_type_labels.get(stage.stage_type)

    def _stage_label(self, bill: Bill, stage: Stage) -> str:
        """The card's stage in the reader's language; Polish stays only where it names a body
        (a committee) or, for RCL, as the numbered original after the translation."""
        if stage.stage_type == "Referral" and stage.committee_code:
            committee = self._committee_display(bill, stage.committee_code, stage.committee_name)
            return f"{esc(self._labels.stage_labels['Referral'])} {esc(committee)}"
        label = self._translate_stage(stage)
        if label is None:
            return esc(stage.stage_name)
        if stage.stage_type == RCL_STAGE_TYPE:
            return f"{esc(label)} ({esc(stage.stage_name)})"
        return esc(label)

    def _action_line(
        self, bill: Bill, today: dt.date, *, agenda_item: AgendaItem | None = None
    ) -> str:
        lb = self._labels
        actions: list[str] = []
        window = bill.consultation
        if bill.wykaz is not None:
            actions.extend(self._wykaz_actions(bill.wykaz))
        elif bill.rcl is not None:
            actions.extend(self._rcl_actions(bill.rcl, window, today))
        elif window is not None and window.is_open(today) and window.end is not None:
            page = window.form_url
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
                for code in codes
                if code
            ]
            if targets:
                text = f"{esc(lb.action_committee)} {', '.join(targets)}"
                sitting = agenda_item if agenda_item and agenda_item.kind == "committee" else None
                sitting = sitting or self._upcoming(bill, today, phase, kind="committee")
                if sitting is not None:
                    text += f" {esc(lb.action_before_sitting)} {self.fmt_date(sitting.date)}"
                actions.append(text)
            hearing = open_hearing(bill, today)
            if hearing is not None:
                text = esc(lb.action_hearing)
                deadline = hearing_application_deadline(hearing)
                if deadline is not None:
                    text += f" {esc(lb.consultation_until)} {self.fmt_date(deadline)}"
                actions.append(text)
        if phase is not None and phase.key == "senate":
            actions.append(esc(lb.action_senate))
        if not actions:
            # Say so, and name the next window, rather than leave the reader guessing.
            nothing = lb.no_action_labels.get(phase.key) if phase is not None else None
            return (
                f"{ICON['action']} <b>{esc(lb.action_now)}:</b> {esc(nothing)}" if nothing else ""
            )
        return f"{ICON['action']} <b>{esc(lb.action_now)}:</b> " + "; ".join(actions)

    def _wykaz_actions(self, entry: WykazEntry) -> list[str]:
        """What a reader can do about a plan: art. 7 of the lobbying act lets anyone file a
        zgłoszenie zainteresowania with the ministry from the moment the entry is published, and
        art. 8 ust. 2 makes that the ticket to the Sejm's public hearing of the bill."""
        if not entry.is_open:
            return []
        lb = self._labels
        organ = entry.organ or lb.wykaz_organ_unknown
        return [esc(lb.action_wykaz_interest.format(organ=organ))]

    def _rcl_actions(
        self, project: RclProject, window: ConsultationWindow | None, today: dt.date
    ) -> list[str]:
        """E-mail to the ministry while the consultation is open (or its deadline unknown), and
        the RCL comment form for as long as the project is with the government."""
        lb = self._labels
        actions: list[str] = []
        polish = lb.action_in_polish.format(wykaz=project.wykaz_number or project.number)
        if window is not None and window.email and (window.end is None or window.is_open(today)):
            text = esc(lb.action_email_ministry.format(email=window.email))
            if window.end is not None:
                text += f" {esc(lb.consultation_until)} {self.fmt_date(window.end)}"
            else:
                text += f" ({esc(lb.consultation_deadline_in_letter)})"
            actions.append(f"{text} {esc(polish)}")
        if project.is_open and not project.sent_to_sejm:
            actions.append(link(project.comment_url, lb.action_rcl_comment))
        return actions

    def _upcoming(
        self, bill: Bill, today: dt.date, phase: Phase, *, kind: str | None = None
    ) -> AgendaItem | None:
        """The soonest future sitting naming the bill, preferring the venue the phase implies."""
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
        return future[0] if future else None

    def _agenda_when(self, item: AgendaItem) -> str:
        lb = self._labels
        if item.kind == "committee":
            when = self.fmt_date(item.date)
            if item.start_time:
                when += f", {item.start_time.strftime('%H:%M')}"
            return esc(when)
        span = self.fmt_date(item.date)
        if item.end_date and item.end_date != item.date:
            span = f"{item.date.day:02d}–{self.fmt_date(item.end_date)}"
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

    def _stage_line(self, stage: Stage) -> str:
        """One bullet of the "new stages" list; may span two lines (voting + club breakdown)."""
        lb = self._labels
        when = f"{self.fmt_date(stage.date)}: " if stage.date else ""
        if stage.stage_type == "Voting" and stage.voting is not None:
            return when + self._voting_lines(stage.voting)
        if stage.stage_type == "SenatePosition" and stage.position:
            text = lb.senate_position_labels.get(stage.position.strip().lower(), stage.position)
            return when + esc(text) + self._print_suffix(stage)
        if stage.stage_type == "Referral" and stage.committee_code:
            name = stage.committee_code
            if stage.committee_name:
                name = f"{stage.committee_name} ({stage.committee_code})"
            return f"{when}{ICON['committee']} {esc(lb.referred_to_committee)}: {esc(name)}"
        if stage.stage_type == "CommitteeReport":
            label = lb.subcommittee_report if stage.sub_committee else lb.committee_report
            line = when + esc(label) + self._print_suffix(stage)
            proposal = _translate(stage.proposal, lb.proposal_labels) or stage.proposal
            if proposal:
                line += f": {esc(lb.proposes)} {esc(proposal)}"
            return line
        if stage.stage_type == "PublicHearing":
            line = when + esc(lb.stage_type_labels["PublicHearing"])
            deadline = hearing_application_deadline(stage)
            if deadline is not None:
                line += f" {esc(lb.consultation_until)} {self.fmt_date(deadline)}"
            return line
        # An RCL stage's bullet keeps the numbered original: the header names it in the
        # reader's language already, and the number is what the RCL page shows.
        translated = None if stage.stage_type == RCL_STAGE_TYPE else self._translate_stage(stage)
        if translated is not None:
            line = when + esc(translated)
            decision = _translate(stage.decision, lb.decision_labels) or stage.decision
            if decision:
                line += f" — {esc(decision)}"
            return line + self._print_suffix(stage)
        parts = [stage.stage_name]
        outcome = stage.decision or stage.position
        if outcome:
            parts.append(f"— {outcome}")
        return when + esc(" ".join(parts)) + self._print_suffix(stage)

    @staticmethod
    def _print_suffix(stage: Stage) -> str:
        return f" (druk {esc(stage.print_number)})" if stage.print_number else ""

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
        budget = MESSAGE_LIMIT - sum(len(b) + 2 for b in kept_head + kept_tail)
        shrunk: list[str] = []
        for block in pending:
            allowed = max(0, budget - 2 * (len(pending) - len(shrunk)))
            if len(block) > allowed:
                block = shrink_block(block, allowed) if allowed > 40 else ""
            if block:
                shrunk.append(block)
                budget -= len(block) + 2
        text = "\n\n".join(kept_head + shrunk + kept_tail)
        return text if len(text) <= MESSAGE_LIMIT else _cut_lines(text, MESSAGE_LIMIT)


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
        parts.append(f"tokens {_k(spent_in)}/{_k(spent_out)}")
        parts += [
            f"{esc(model.removeprefix('claude-'))} {_k(u.input + u.cache_read)}"
            for model, u in outcome.usage.items()
            if len(outcome.usage) > 1
        ]
        cost = cost_usd(outcome.usage)
        if cost is not None:
            parts.append(f"≈ ${cost:.2f}" if cost >= 0.01 else f"≈ ${cost:.3f}")
    return f"⏱ {' · '.join(parts)}" if parts else ""


def _tokens_line(report: RunReport) -> str:
    """`tokens in/out: 12345/678 · cache read 4.0k · opus-5 10.3k/0.6k · sonnet-5 5.8k/0.1k ·
    ≈ $0.06`. Cache reads are shown apart from the uncached input: whether the prompt cache
    ever hits is otherwise invisible. Empty when the model was never called."""
    if not (report.llm_input_tokens or report.llm_output_tokens or report.llm_usage):
        return ""
    parts = [f"tokens in/out: {report.llm_input_tokens}/{report.llm_output_tokens}"]
    cached = sum(u.cache_read for u in report.llm_usage.values())
    if cached:
        parts.append(f"cache read {_k(cached)}")
    if len(report.llm_usage) > 1:
        parts += [
            f"{esc(model.removeprefix('claude-'))} {_k(u.input + u.cache_read)}/{_k(u.output)}"
            for model, u in report.llm_usage.items()
        ]
    cost = cost_usd(report.llm_usage)
    if cost is not None and report.llm_usage:
        parts.append(f"≈ ${cost:.2f}" if cost >= 0.01 else f"≈ ${cost:.3f}")
    return " · ".join(parts)


def _k(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)


def _section(icon: str, title: str, *lines: str, empty: str = "") -> str:
    """`🔎 <b>title</b>` followed by the non-empty lines, one per row; `empty` is the row shown
    when there are none."""
    rows = [line for line in lines if line] or ([empty] if empty else [])
    return "\n".join([f"{icon} <b>{esc(title)}</b>", *rows])


def _counters(*items: tuple[str, int]) -> str:
    """`Sejm: 3 new · prefilter hits: 1`: the templates whose counter is not zero, filled in and
    joined; empty when every counter is zero."""
    return " · ".join(template.format(value) for template, value in items if value)


def _verdict_ref(verdict: AnalysisVerdict) -> str:
    """`druk 2695` / `RCL/12414402` / `RPW/29075/2026`, linked to the process page when the term
    is known (reports stored before the field existed have none)."""
    number = verdict.number
    plain = is_rcl_number(number) or is_pre_print_number(number)
    label = esc(number) if plain else f"druk {esc(number)}"
    if verdict.term is None:
        return label
    return link(process_web_url(verdict.term, number), label)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tag_safe(number: str) -> str:
    return "".join(ch for ch in number if ch.isalnum() or ch == "_")


def _number_tag(term: int, number: str, wykaz_number: str | None) -> str:
    """`#RCL_UC104` (the project id when the wykaz number is unknown), `#RPW_29075_2026`,
    `#kadencja10druk3039`.

    A planned bill takes the same `#RCL_UD408`: the wykaz number is the government project's
    identity from the plan through RCL to the druk, and one search finds the whole thread."""
    if is_wykaz_number(number):
        return "#RCL_" + _tag_safe(wykaz_entry_number(number))
    if is_rcl_number(number):
        return "#RCL_" + _tag_safe(wykaz_number or number.removeprefix(RCL_PREFIX))
    if is_pre_print_number(number):
        return "#" + _tag_safe(number.replace("/", "_"))
    return f"#kadencja{term}druk{_tag_safe(number)}"


def _cut_lines(text: str, limit: int) -> str:
    """Drop whole lines from the end until `text` fits `limit` (every line of a message is
    HTML-balanced on its own, so no tag is split). Better a card without its last lines than
    Telegram's 400 and a lost post; a message without a single fitting line is left alone."""
    cut = text.rfind("\n", 0, limit)
    return text[:cut].rstrip() if cut > 0 else text


def shrink_block(block: str, allowed: int) -> str:
    """Trim a block to `allowed` chars keeping its HTML well-formed.

    Blocks are "<b>header</b>\n<escaped body>"; cutting is only allowed inside the body, at a line
    boundary, so no tag or entity is ever split. A block that cannot keep its header is dropped.
    """
    if block.endswith("</pre>"):
        opening = block.index("<pre>") + len("<pre>")
        inner = block[opening : -len("</pre>")]
        room = allowed - opening - len("</pre>")
        return block[:opening] + fit(inner, max(room, 0)) + "</pre>" if room > 10 else ""
    header_end = block.find("\n")
    if header_end == -1 or header_end + 1 >= allowed:
        return ""
    head, body = block[: header_end + 1], block[header_end + 1 :]
    kept = fit(body, allowed - len(head))
    return head + kept if kept.strip(ELLIPSIS) else ""
