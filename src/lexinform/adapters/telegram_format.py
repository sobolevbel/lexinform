"""Renders Telegram HTML messages from domain models. Pure functions, no I/O.

Telegram limit: 4096 characters per message. Everything derived from external data is passed
through html.escape; only our own markup is raw HTML.
"""

import datetime as dt
import html
from dataclasses import dataclass

from lexinform.i18n import Labels, labels_for
from lexinform.keywords import KEYWORD_PATTERNS
from lexinform.models import (
    RCL_STAGE_TYPE,
    ActInfo,
    AgendaItem,
    Bill,
    ConsultationWindow,
    Phase,
    PrintInfo,
    RclProject,
    RunReport,
    Stage,
    StatusChange,
    VotingSummary,
    committee_web_url,
    flatten_stages,
    next_phase,
)
from lexinform.pricing import cost_usd

MESSAGE_LIMIT = 4096
ELLIPSIS = "…"

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
    "changed": "🆕",
    "passed": "✅",
    "closed": "🏁",
    "note": "ℹ️",
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
}
_COMMITTEE_PHASES = {"first_reading_committee", "committee_work", "senate_amendments"}
_SITTING_PHASES = {"first_reading_sitting", "second_reading", "third_reading", "senate_amendments"}
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

    def __init__(self, language: str = "ru") -> None:
        self._labels: Labels = labels_for(language)

    # ------------------------------------------------------------------ new bill card

    def new_bill(
        self, bill: Bill, print_info: PrintInfo | None, *, today: dt.date | None = None
    ) -> RenderedMessage:
        """The card. `today` decides whether the public consultation still counts as open."""
        if bill.analysis is None:
            raise ValueError(f"bill {bill.number} has no analysis")
        a = bill.analysis.analysis
        lb = self._labels
        s = bill.summary

        head_label = lb.rcl_header if bill.rcl is not None else lb.new_bill_header
        header = (
            f"{ICON['new_bill']} <b>{esc(head_label)} — {self._number_label(bill)}</b>\n\n"
            f"<b>{esc(s.title)}</b>"
        )
        meta = (
            f"{score_icon(a.score)} <b>{esc(lb.importance)}:</b> {importance_bar(a.score)} "
            f"{a.score}/5 — {esc(lb.score_labels.get(a.score, ''))}\n"
            f"{ICON['category']} <b>{esc(lb.category)}:</b> "
            f"{esc(lb.category_labels.get(a.category, a.category.value))}"
        )
        summary_block = f"{ICON['about']} <b>{esc(lb.about)}</b>\n{esc(a.summary.strip())}"
        changes_block = ""
        if a.key_changes:
            bullets = "\n".join(f"• {esc(c.strip())}" for c in a.key_changes if c.strip())
            changes_block = f"{ICON['key_changes']} <b>{esc(lb.key_changes)}</b>\n{bullets}"

        details: list[str] = []
        if a.practical_impact.strip():
            details.append(
                f"{ICON['practical']} <b>{esc(lb.practical_impact)}:</b> "
                f"{esc(a.practical_impact.strip())}"
            )
        if a.affected_groups:
            details.append(
                f"{ICON['affected']} <b>{esc(lb.affected)}:</b> "
                f"{esc(', '.join(g.strip() for g in a.affected_groups))}"
            )
        details.append(
            f"{ICON['effective']} <b>{esc(lb.effective_date)}:</b> "
            f"{esc(a.effective_date.strip() if a.effective_date else lb.effective_date_unknown)}"
        )
        consultation = self._consultation_line(bill)
        if consultation:
            details.append(consultation)
        steps = self._steps_block(bill, today or dt.date.today())
        if steps:
            details.append(steps)

        # Short one-line facts are grouped compactly; the paragraphs above are
        # separated by blank lines so they read as distinct blocks.
        meta_lines: list[str] = []
        last = bill.last_stage
        if last is not None:
            when = f" ({self.fmt_date(last.date)})" if last.date else ""
            meta_lines.append(
                f"{ICON['stage']} <b>{esc(lb.stage)}:</b> {esc(last.stage_name)}{when}"
            )
        elif bill.rcl is not None:
            meta_lines.append(f"{ICON['stage']} <b>{esc(lb.stage)}:</b> {esc(lb.rcl_no_stage)}")
        elif bill.is_pre_print:
            meta_lines.append(f"{ICON['stage']} <b>{esc(lb.stage)}:</b> {esc(lb.pre_print_stage)}")
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
        details_block = "\n\n".join(details)

        if bill.rcl is not None:
            links = self._rcl_links(bill.rcl)
        elif bill.is_pre_print:
            links = [link(s.web_url, lb.link_submission_pdf)]
        else:
            links = [link(s.web_url, lb.link_process)]
            pdf = print_info.main_pdf if print_info else None
            if pdf is not None:
                links.append(link(pdf.url, lb.link_pdf))
            if s.rcl_link:
                links.append(link(s.rcl_link, lb.link_rcl))
        links_block = f"{ICON['links']} " + " | ".join(links)

        # Each tag answers one search: bills of the term, by importance, by topic, where an
        # opinion can still be sent, about citizens of Ukraine, government projects before the
        # Sejm, and this bill's whole thread.
        tags = " ".join(
            [
                self._number_tag(bill),
                f"#{lb.tag_importance}{a.score}",
                f"#{lb.category_tags.get(a.category, a.category.value)}",
                *([f"#{lb.tag_consultations}"] if _consultation_open(bill, today) else []),
                *([f"#{lb.tag_ukraine}"] if _about_ukraine(bill) else []),
                *([f"#{lb.tag_rcl}"] if bill.rcl is not None else []),
                self._term_tag(s.term),
            ]
        )

        fixed = [header, meta, details_block, links_block, tags]
        text = self._assemble(fixed, flexible=[summary_block, changes_block])
        return RenderedMessage(text=text)

    # ------------------------------------------------------------------ status update

    def status_update(
        self, bill: Bill, change: StatusChange, *, today: dt.date | None = None
    ) -> RenderedMessage:
        lb = self._labels
        s = bill.summary
        analysis = bill.analysis.analysis if bill.analysis else None

        header = (
            f"{ICON['update']} <b>{esc(lb.update_header)} — {self._number_label(bill)}</b>\n\n"
            f"<b>{esc(s.title)}</b>"
        )
        badge = ""
        if analysis is not None:
            badge = (
                f"{score_icon(analysis.score)} {importance_bar(analysis.score)} "
                f"{analysis.score}/5 — "
                f"{esc(lb.category_labels.get(analysis.category, analysis.category.value))}"
            )

        stage_lines = [f"• {self._stage_line(st)}" for st in change.new_stages]
        stages_block = (
            f"{ICON['new_stages']} <b>{esc(lb.new_stages)}</b>\n" + "\n".join(stage_lines)
            if stage_lines
            else ""
        )
        closure = ""
        if change.withdrawn:
            closure = f"{ICON['closed']} {esc(lb.process_withdrawn)}"
        elif change.closure_detected:
            icon = ICON["passed"] if change.passed else ICON["closed"]
            closure = f"{icon} {esc(lb.process_passed if change.passed else lb.process_closed)}"
        if bill.linked_number and bill.has_process and change.old_fingerprint == bill.linked_number:
            assigned = f"{ICON['print']} {esc(lb.print_assigned)}: <b>{esc(bill.number)}</b>"
            closure = f"{assigned}\n{closure}" if closure else assigned
        elif bill.rcl is not None and bill.rcl.sent_to_sejm and _reaches_sejm(change):
            closure = f"{ICON['print']} {esc(lb.rcl_sent_to_sejm)}"
        consultation = self._consultation_line(bill)
        steps = "" if change.withdrawn else self._steps_block(bill, today or dt.date.today())

        summary_block = ""
        changes_block = ""
        if analysis is not None:
            summary_block = (
                f"{ICON['about']} <b>{esc(lb.current_summary)}</b>\n{esc(analysis.summary.strip())}"
            )
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

        links = [link(s.web_url, lb.link_rcl_project if bill.rcl is not None else lb.link_process)]
        text_after3 = next((st.text_after3 for st in change.new_stages if st.text_after3), None)
        if text_after3:
            links.append(link(text_after3, lb.link_text_after3))
        elif bill.analysis and bill.analysis.source_url and change.content_changed:
            links.append(link(bill.analysis.source_url, lb.link_pdf))
        links_block = f"{ICON['links']} " + " | ".join(links)
        # Event tags only when the reply carries the event a reader would search for.
        tags = " ".join(
            [f"#{lb.event_tags[key]}" for key in _event_keys(change) if key in lb.event_tags]
            + [self._number_tag(bill)]
        )

        fixed = [header, badge, closure, consultation, steps, links_block, tags]
        text = self._assemble(fixed, flexible=[stages_block, changes_block, summary_block])
        return RenderedMessage(text=text)

    # ------------------------------------------------------------------ published act

    def act_published(self, bill: Bill) -> RenderedMessage:
        lb = self._labels
        act = bill.act
        if act is None:
            raise ValueError(f"bill {bill.number} has no act")
        header = (
            f"{ICON['published']} <b>{esc(lb.act_published_header)} — "
            f"{self._number_label(bill)}</b>\n\n<b>{esc(act.title or bill.summary.title)}</b>"
        )
        lines = [f"{ICON['journal']} <b>{esc(lb.journal)}:</b> {esc(act.display_address)}"]
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
                f"{ICON['effective']} <b>{esc(lb.enters_into_force)}:</b> "
                f"{self.fmt_date(act.entry_into_force)}"
            )
        lines.append(f"{ICON['note']} <i>{esc(lb.partial_vacatio_note)}</i>")
        facts = "\n".join(lines)
        links_block = f"{ICON['links']} " + " | ".join(self._act_links(bill, act))
        tags = f"#{lb.tag_published} {self._number_tag(bill)}"
        return RenderedMessage(text=self._assemble([header, facts, links_block, tags], flexible=[]))

    def in_force(self, bill: Bill) -> RenderedMessage:
        lb = self._labels
        act = bill.act
        if act is None or act.entry_into_force is None:
            raise ValueError(f"bill {bill.number} has no entry-into-force date")
        header = (
            f"{ICON['in_force']} <b>{esc(lb.in_force_header)} — {self._number_label(bill)}</b>\n\n"
            f"<b>{esc(act.title or bill.summary.title)}</b>"
        )
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
                practical = (
                    f"{ICON['practical']} <b>{esc(lb.practical_impact)}:</b> "
                    f"{esc(a.practical_impact.strip())}"
                )
        links_block = f"{ICON['links']} " + " | ".join(self._act_links(bill, act))
        tags = f"#{lb.tag_in_force} {self._number_tag(bill)}"
        fixed = [header, facts, links_block, tags]
        return RenderedMessage(text=self._assemble(fixed, flexible=[summary_block, practical]))

    def consultation_deadline(self, bill: Bill, *, today: dt.date) -> RenderedMessage:
        """Reply under the card a few days before the public consultation closes."""
        lb = self._labels
        window = bill.consultation
        if window is None or window.end is None:
            raise ValueError(f"bill {bill.number} has no consultation end date")
        days_left = (window.end - today).days
        header = (
            f"{ICON['consultation']} <b>{esc(lb.consultation_deadline_header)} — "
            f"{self._number_label(bill)}</b>\n\n<b>{esc(bill.summary.title)}</b>"
        )
        countdown = (
            esc(lb.consultation_last_day)
            if days_left <= 0
            else f"{esc(lb.consultation_days_left)}: {days_left}"
        )
        where = self._consultation_where(bill, window, sejm_label=lb.consultation_hint)
        facts = (
            f"{ICON['effective']} <b>{esc(lb.consultation)}:</b> {esc(lb.consultation_until)} "
            f"{self.fmt_date(window.end)} · {countdown}\n"
            f"{ICON['action']} {where}"
        )
        next_step = self._next_step_line(bill, today)
        summary_block = ""
        if bill.analysis is not None:
            a = bill.analysis.analysis
            summary_block = f"{ICON['about']} <b>{esc(lb.about)}</b>\n{esc(a.summary.strip())}"
        links_block = f"{ICON['links']} " + " | ".join(self._consultation_links(bill, window))
        tags = f"#{lb.tag_consultations} {self._number_tag(bill)}"
        fixed = [header, facts, next_step, links_block, tags]
        return RenderedMessage(text=self._assemble(fixed, flexible=[summary_block]))

    def consultation_results(self, bill: Bill, *, today: dt.date | None = None) -> RenderedMessage:
        """Reply under the card once the Sejm publishes the opinions received."""
        lb = self._labels
        window = bill.consultation
        if window is None:
            raise ValueError(f"bill {bill.number} had no public consultation")
        header = (
            f"{ICON['consultation']} <b>{esc(lb.consultation_results_header)} — "
            f"{self._number_label(bill)}</b>\n\n<b>{esc(bill.summary.title)}</b>"
        )
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
            facts = (
                f"{ICON['effective']} <b>{esc(lb.consultation)}:</b> "
                f"{self._consultation_period(window)}\n{facts}"
            )
        steps = self._steps_block(bill, today or dt.date.today())
        links_block = f"{ICON['links']} " + " | ".join(self._consultation_links(bill, window))
        tags = f"#{lb.tag_consultations} {self._number_tag(bill)}"
        return RenderedMessage(
            text=self._assemble([header, facts, steps, links_block, tags], flexible=[])
        )

    # ------------------------------------------------------------------ sittings

    def agenda(
        self, bill: Bill, item: AgendaItem, *, today: dt.date | None = None
    ) -> RenderedMessage:
        """Reply under the card: the bill is on the agenda of a committee or Sejm sitting."""
        lb = self._labels
        s = bill.summary
        is_committee = item.kind == "committee"
        head_label = lb.agenda_committee_header if is_committee else lb.agenda_sejm_header
        header = (
            f"{ICON['calendar']} <b>{esc(head_label)} — {self._number_label(bill)}</b>\n\n"
            f"<b>{esc(s.title)}</b>"
        )
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
            lines.append(f"{ICON['agenda']} <b>{esc(lb.agenda_item)}:</b> {esc(item.text)}")
        facts = "\n".join(lines)
        action = self._action_line(bill, today or dt.date.today(), agenda_item=item)
        links = [link(s.web_url, lb.link_process)]
        if item.video_url:
            links.append(link(item.video_url, lb.link_video))
        if is_committee and item.committee_code:
            links.append(link(committee_web_url(s.term, item.committee_code), lb.link_committee))
        links_block = f"{ICON['links']} " + " | ".join(links)
        tag = lb.tag_committee_sitting if is_committee else lb.tag_sejm_sitting
        tags = f"#{tag} {self._number_tag(bill)}"
        summary_block = ""
        if bill.analysis is not None:
            a = bill.analysis.analysis
            summary_block = (
                f"{ICON['about']} <b>{esc(lb.current_summary)}</b>\n{esc(a.summary.strip())}"
            )
        fixed = [header, facts, action, links_block, tags]
        return RenderedMessage(text=self._assemble(fixed, flexible=[summary_block]))

    def _act_links(self, bill: Bill, act: ActInfo) -> list[str]:
        lb = self._labels
        links = [link(bill.summary.web_url, lb.link_process)]
        isap = act.isap_url or bill.summary.isap_url
        if isap:
            links.append(link(isap, lb.link_isap))
        if act.text_pdf_url:
            links.append(link(act.text_pdf_url, lb.link_act_pdf))
        return links

    # ------------------------------------------------------------------ run report

    def run_report(self, report: RunReport, log_lines: list[str]) -> RenderedMessage:
        lb = self._labels
        status = "✅" if report.ok else "❌"
        duration = report.duration_seconds
        head = (
            f"<b>{status} {esc(lb.run_report_title)}</b>\n"
            f"mode: {esc(report.mode)} · since: {esc(report.since.strftime('%Y-%m-%d %H:%M'))}"
            + (f" · {duration}s" if duration is not None else "")
        )
        counters = "\n".join(
            [
                f"discovered: {report.discovered} (+{report.pre_print_discovered} without print"
                f" number) · prefilter hits: {report.prefilter_hits} · RCL: "
                f"{report.rcl_discovered} (hits {report.rcl_prefilter_hits})",
                f"text prefilter: checked {report.text_prefilter_checked} · "
                f"hits {report.text_prefilter_hits}",
                f"analyzed: {report.analyzed} · triaged out: {report.triaged_out} · "
                f"failures: {report.analysis_failures}",
                f"published: {report.published} · tracked: {report.tracked} · "
                f"updates: {report.updates} · re-analyzed: {report.reanalyzed} · "
                f"linked: {report.linked}",
                f"acts published: {report.acts_published} · in force: {report.in_force_posted}"
                f" · consultation reminders: {report.consultation_reminders}"
                f" · results: {report.consultation_results_posted}"
                f" · agenda: {report.agenda_posted}",
                _tokens_line(report),
            ]
            + (
                [
                    "timing: "
                    + " · ".join(
                        f"{esc(name)} {secs:.1f}s" for name, secs in report.phase_seconds.items()
                    )
                ]
                if report.phase_seconds
                else []
            )
        )
        errors = ""
        if report.errors:
            errors = "<b>errors</b>\n" + "\n".join(f"• {esc(e)}" for e in report.errors)
        rejected = ""
        if report.rejected:
            rejected = "<b>analysed, not published</b>\n" + "\n".join(
                f"• druk {esc(v.number)} · {esc(v.reason)} · {esc(_clip(v.title, 110))}"
                for v in report.rejected
            )
        logs = ""
        if log_lines:
            logs = "<b>warnings</b>\n<pre>" + esc("\n".join(log_lines)) + "</pre>"
        text = self._assemble([head, counters, errors], flexible=[rejected, logs])
        return RenderedMessage(text=text)

    # ------------------------------------------------------------------ helpers

    def fmt_date(self, value: dt.date) -> str:
        return value.strftime(self._labels.date_format)

    def _number_label(self, bill: Bill) -> str:
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
        if bill.rcl is not None:
            return "#RCL_" + _tag_safe(bill.rcl.wykaz_number or str(bill.rcl.id))
        if bill.is_pre_print:
            return "#" + _tag_safe(bill.number.replace("/", "_"))
        return f"#kadencja{bill.term}druk{_tag_safe(bill.number)}"

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
        """ "Инициатор: правительственный — Minister … · номер в wykazie: UC164   Опубликован на
        RCL: 31.08.2026" for RCL projects; applicant, signatories and document date otherwise."""
        lb = self._labels
        s = bill.summary
        applicant = esc(lb.applicant_labels.get(s.applicant_type, s.applicant_type.value))
        if bill.rcl is not None:
            project = bill.rcl
            who = f"{applicant} — {esc(project.applicant)}"
            if project.wykaz_number:
                who += f" · {esc(lb.rcl_wykaz)}: {esc(project.wykaz_number)}"
            when = f"{ICON['doc_date']} <b>{esc(lb.rcl_published)}:</b> "
            return f"{ICON['applicant']} <b>{esc(lb.applicant)}:</b> {who}   {when}" + esc(
                self.fmt_date(project.created)
            )
        doc_date = self.fmt_date(s.document_date) if s.document_date else "—"
        date_label = lb.received if bill.is_pre_print else lb.document_date
        return (
            f"{ICON['applicant']} <b>{esc(lb.applicant)}:</b> {applicant}"
            f"{self._authors_suffix(bill)}   "
            f"{ICON['doc_date']} <b>{esc(date_label)}:</b> {esc(doc_date)}"
        )

    def _text_note(self, bill: Bill) -> str:
        """Why the analysis saw less than the whole text, if it did."""
        lb = self._labels
        record = bill.analysis
        if record is None:
            return ""
        if record.text_source == "metadata_only":
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

    def _consultation_line(self, bill: Bill) -> str:
        window = bill.consultation
        if window is None:
            return ""
        lb = self._labels
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
        """ "до 08.09.2026 (7 дней с даты письма)"."""
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

    # ------------------------------------------------------------------ next step / action

    def _steps_block(self, bill: Bill, today: dt.date) -> str:
        """ "What comes next" (with a date when a sitting is scheduled) and "what you can do"."""
        lines = [self._next_step_line(bill, today), self._action_line(bill, today)]
        return "\n".join(line for line in lines if line)

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
        suffix = f" · {self._agenda_when(upcoming)}" if upcoming else ""
        return f"{ICON['next']} <b>{esc(lb.next_step)}:</b> {esc(text)}{suffix}"

    def _action_line(
        self, bill: Bill, today: dt.date, *, agenda_item: AgendaItem | None = None
    ) -> str:
        lb = self._labels
        actions: list[str] = []
        window = bill.consultation
        if bill.rcl is not None:
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
        if phase is not None and phase.key in _COMMITTEE_PHASES:
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
            if _hearing_open(bill, today):
                actions.append(esc(lb.action_hearing))
        if not actions:
            return ""
        return f"{ICON['action']} <b>{esc(lb.action_now)}:</b> " + "; ".join(actions)

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
        if phase.key in _COMMITTEE_PHASES:
            preferred.append("committee")
        if phase.key in _SITTING_PHASES:
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
            if stage.committee_name:
                name = f"{stage.committee_name} ({stage.committee_code})"
                return f"{when}{ICON['committee']} {esc(lb.referred_to_committee)}: {esc(name)}"
            return f"{when}{esc(stage.stage_name)} [{esc(stage.committee_code)}]"
        label = lb.stage_type_labels.get(stage.stage_type)
        if label is not None:
            line = when + esc(label)
            if stage.decision:
                line += f" — {esc(stage.decision)}"
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
    def _assemble(fixed: list[str], *, flexible: list[str]) -> str:
        """Join blocks with blank lines; shrink flexible blocks (in order) until under the limit."""
        fixed = [b for b in fixed if b]
        flexible = [b for b in flexible if b]
        budget = MESSAGE_LIMIT - sum(len(b) + 2 for b in fixed)
        shrunk: list[str] = []
        for block in flexible:
            allowed = max(0, budget - 2 * (len(flexible) - len(shrunk)))
            if len(block) > allowed:
                block = shrink_block(block, allowed) if allowed > 40 else ""
            if block:
                shrunk.append(block)
                budget -= len(block) + 2
        # Order: header, meta, summary, changes, details, links, tags
        ordered = fixed[:2] + shrunk + fixed[2:]
        return "\n\n".join(b for b in ordered if b)


_SENATE_STAGES = {"SenatePosition", "SenatePositionConsideration"}
_PRESIDENT_STAGES = {"ToPresident", "PresidentSignature"}


def _event_keys(change: StatusChange) -> list[str]:
    """Which searchable events a status update carries, in display order."""
    types = {stage.stage_type for stage in flatten_stages(tuple(change.new_stages))}
    keys = []
    if "Voting" in types or any(st.voting for st in change.new_stages):
        keys.append("voting")
    if types & _SENATE_STAGES:
        keys.append("senate")
    if types & _PRESIDENT_STAGES:
        keys.append("president")
    if "Veto" in types:
        keys.append("veto")
    if change.content_changed:
        keys.append("amendments")
    if change.withdrawn:
        keys.append("withdrawn")
    return keys


def _consultation_open(bill: Bill, today: dt.date | None) -> bool:
    window = bill.consultation
    return window is not None and window.is_open(today or dt.date.today())


def _reaches_sejm(change: StatusChange) -> bool:
    """The update carries the RCL stage "Skierowanie projektu ustawy do Sejmu"."""
    return any(
        st.stage_type == RCL_STAGE_TYPE and "sejm" in st.stage_name.lower()
        for st in change.new_stages
    )


def _hearing_open(bill: Bill, today: dt.date) -> bool:
    """A public hearing is announced and has not taken place yet."""
    return any(
        st.stage_type == "PublicHearing" and (st.date is None or st.date >= today)
        for st in flatten_stages(bill.stages)
    )


_UKRAINE = next(p.regex for p in KEYWORD_PATTERNS if p.name == "obywatele_ukrainy")


def _about_ukraine(bill: Bill) -> bool:
    """Prefilter hit on "obywatele Ukrainy" (title or text), or the title says so itself."""
    hits = {hit.removeprefix("text:") for hit in bill.prefilter_hits}
    if "obywatele_ukrainy" in hits:
        return True
    s = bill.summary
    return bool(_UKRAINE.search(f"{s.title} {s.description or ''}"))


def _tokens_line(report: RunReport) -> str:
    """`tokens in/out: 12345/678 · opus-5 10.3k/0.6k · sonnet-5 5.8k/0.1k · ≈ $0.06`."""
    parts = [f"tokens in/out: {report.llm_input_tokens}/{report.llm_output_tokens}"]
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


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tag_safe(number: str) -> str:
    return "".join(ch for ch in number if ch.isalnum() or ch == "_")


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
