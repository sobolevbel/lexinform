"""Renders Telegram HTML messages from domain models. Pure functions, no I/O.

Telegram limit: 4096 characters per message. Everything derived from external data is passed
through html.escape; only our own markup is raw HTML.
"""

from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass

from lexinform.i18n import Labels, labels_for
from lexinform.models import (
    ActInfo,
    Bill,
    PrintInfo,
    RunReport,
    Stage,
    StatusChange,
    VotingSummary,
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
}
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
    def __init__(
        self, language: str = "ru", *, api_base_url: str = "https://api.sejm.gov.pl"
    ) -> None:
        self._labels: Labels = labels_for(language)
        self._api_base_url = api_base_url.rstrip("/")

    # ------------------------------------------------------------------ new bill card

    def new_bill(self, bill: Bill, print_info: PrintInfo | None) -> RenderedMessage:
        if bill.analysis is None:
            raise ValueError(f"bill {bill.number} has no analysis")
        a = bill.analysis.analysis
        lb = self._labels
        s = bill.summary

        header = (
            f"{ICON['new_bill']} <b>{esc(lb.new_bill_header)} — {self._number_label(bill)}</b>\n\n"
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

        # Short one-line facts are grouped compactly; the paragraphs above are
        # separated by blank lines so they read as distinct blocks.
        meta_lines: list[str] = []
        last = bill.last_stage
        if last is not None:
            when = f" ({self.fmt_date(last.date)})" if last.date else ""
            meta_lines.append(
                f"{ICON['stage']} <b>{esc(lb.stage)}:</b> {esc(last.stage_name)}{when}"
            )
        elif bill.is_pre_print:
            meta_lines.append(f"{ICON['stage']} <b>{esc(lb.stage)}:</b> {esc(lb.pre_print_stage)}")
        applicant = lb.applicant_labels.get(s.applicant_type, s.applicant_type.value)
        doc_date = self.fmt_date(s.document_date) if s.document_date else "—"
        date_label = lb.received if bill.is_pre_print else lb.document_date
        meta_lines.append(
            f"{ICON['applicant']} <b>{esc(lb.applicant)}:</b> {esc(applicant)}"
            f"{self._authors_suffix(bill)}   "
            f"{ICON['doc_date']} <b>{esc(date_label)}:</b> {esc(doc_date)}"
        )
        if s.prints_considered_jointly:
            meta_lines.append(
                f"{esc(lb.joint_prints)} {esc(', '.join(s.prints_considered_jointly))}"
            )
        if any(h.startswith("text:") for h in bill.prefilter_hits):
            meta_lines.append(f"{ICON['search']} <i>{esc(lb.found_in_text)}</i>")
        if bill.is_pre_print and bill.analysis.text_source == "metadata_only":
            meta_lines.append(f"{ICON['note']} <i>{esc(lb.pre_print_note)}</i>")
        elif bill.analysis.truncated or bill.analysis.text_source == "metadata_only":
            meta_lines.append(f"{ICON['note']} <i>{esc(lb.partial_text_note)}</i>")
        details.append("\n".join(meta_lines))
        details_block = "\n\n".join(details)

        if bill.is_pre_print:
            links = [link(s.web_url, lb.link_submission_pdf)]
        else:
            links = [link(s.web_url, lb.link_process)]
        pdf = print_info.main_pdf if print_info else None
        if pdf is not None:
            links.append(link(pdf.url, lb.link_pdf))
        if s.rcl_link:
            links.append(link(s.rcl_link, lb.link_rcl))
        links_block = f"{ICON['links']} " + " | ".join(links)

        tags = " ".join(
            [
                f"#{lb.tag_importance}{a.score}",
                f"#{lb.category_tags.get(a.category, a.category.value)}",
                self._number_tag(bill),
                f"#Sejm{s.term}",
            ]
        )

        fixed = [header, meta, details_block, links_block, tags]
        text = self._assemble(fixed, flexible=[summary_block, changes_block])
        return RenderedMessage(text=text)

    # ------------------------------------------------------------------ status update

    def status_update(self, bill: Bill, change: StatusChange) -> RenderedMessage:
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
        if (
            bill.linked_number
            and not bill.is_pre_print
            and change.old_fingerprint == bill.linked_number
        ):
            assigned = f"{ICON['print']} {esc(lb.print_assigned)}: <b>{esc(bill.number)}</b>"
            closure = f"{assigned}\n{closure}" if closure else assigned
        consultation = self._consultation_line(bill)

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

        links = [link(s.web_url, lb.link_process)]
        text_after3 = next((st.text_after3 for st in change.new_stages if st.text_after3), None)
        if text_after3:
            links.append(link(text_after3, lb.link_text_after3))
        elif bill.analysis and bill.analysis.source_url and change.content_changed:
            links.append(link(bill.analysis.source_url, lb.link_pdf))
        links_block = f"{ICON['links']} " + " | ".join(links)
        tags = f"#{lb.tag_update} {self._number_tag(bill)} #Sejm{s.term}"

        fixed = [header, badge, closure, consultation, links_block, tags]
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
        tags = f"#{lb.tag_published} {self._number_tag(bill)} #Sejm{bill.term}"
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
        tags = f"#{lb.tag_in_force} {self._number_tag(bill)} #Sejm{bill.term}"
        fixed = [header, facts, links_block, tags]
        return RenderedMessage(text=self._assemble(fixed, flexible=[summary_block, practical]))

    def consultation_deadline(self, bill: Bill, *, today: dt.date) -> RenderedMessage:
        """Reply under the card a few days before the public consultation closes."""
        lb = self._labels
        sub = bill.submission
        if sub is None or sub.consultation_end is None:
            raise ValueError(f"bill {bill.number} has no consultation end date")
        days_left = (sub.consultation_end - today).days
        header = (
            f"{ICON['consultation']} <b>{esc(lb.consultation_deadline_header)} — "
            f"{self._number_label(bill)}</b>\n\n<b>{esc(bill.summary.title)}</b>"
        )
        countdown = (
            esc(lb.consultation_last_day)
            if days_left <= 0
            else f"{esc(lb.consultation_days_left)}: {days_left}"
        )
        facts = (
            f"{ICON['effective']} <b>{esc(lb.consultation)}:</b> {esc(lb.consultation_until)} "
            f"{self.fmt_date(sub.consultation_end)} · {countdown}\n"
            f"{ICON['note']} {esc(lb.consultation_hint)}"
        )
        summary_block = ""
        if bill.analysis is not None:
            a = bill.analysis.analysis
            summary_block = f"{ICON['about']} <b>{esc(lb.about)}</b>\n{esc(a.summary.strip())}"
        links_block = f"{ICON['links']} " + link(bill.summary.web_url, lb.link_process)
        tags = f"#{lb.tag_consultations} {self._number_tag(bill)} #Sejm{bill.term}"
        fixed = [header, facts, links_block, tags]
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
                f" number) · prefilter hits: {report.prefilter_hits}",
                f"text prefilter: checked {report.text_prefilter_checked} · "
                f"hits {report.text_prefilter_hits}",
                f"analyzed: {report.analyzed} · triaged out: {report.triaged_out} · "
                f"failures: {report.analysis_failures}",
                f"published: {report.published} · tracked: {report.tracked} · "
                f"updates: {report.updates} · re-analyzed: {report.reanalyzed} · "
                f"linked: {report.linked}",
                f"acts published: {report.acts_published} · in force: {report.in_force_posted}"
                f" · consultation reminders: {report.consultation_reminders}",
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
        if bill.is_pre_print:
            return f"{esc(bill.number)} ({esc(self._labels.no_print_yet)})"
        return f"druk nr {esc(bill.number)}"

    @staticmethod
    def _number_tag(bill: Bill) -> str:
        if bill.is_pre_print:
            return "#" + _tag_safe(bill.number.replace("/", "_"))
        return f"#druk{_tag_safe(bill.number)}"

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

    def _consultation_line(self, bill: Bill) -> str:
        sub = bill.submission
        if sub is None or not sub.public_consultation or sub.consultation_end is None:
            return ""
        lb = self._labels
        period = f"{esc(lb.consultation_until)} {self.fmt_date(sub.consultation_end)}"
        if sub.consultation_start:
            period = (
                f"{self.fmt_date(sub.consultation_start)} — {self.fmt_date(sub.consultation_end)}"
            )
        return (
            f"{ICON['consultation']} <b>{esc(lb.consultation)}:</b> {period} — "
            f"{esc(lb.consultation_hint)}"
        )

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
                head = f"{ICON['committee']} {esc(lb.referred_to_committee)}: {esc(name)}"
                return f"{when}{head} — {esc(lb.committee_hint)}"
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
                block = _shrink_block(block, allowed) if allowed > 40 else ""
            if block:
                shrunk.append(block)
                budget -= len(block) + 2
        # Order: header, meta, summary, changes, details, links, tags
        ordered = fixed[:2] + shrunk + fixed[2:]
        return "\n\n".join(b for b in ordered if b)


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


def _shrink_block(block: str, allowed: int) -> str:
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
