"""Renders Telegram HTML messages from domain models. Pure functions, no I/O.

Telegram limit: 4096 characters per message. Everything derived from external data is passed
through html.escape; only our own markup is raw HTML.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from lexinform.i18n import Labels, labels_for
from lexinform.models import Bill, PrintInfo, RunReport, Stage, StatusChange

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
}


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
            f"{ICON['new_bill']} <b>{esc(lb.new_bill_header)} — druk nr {esc(s.number)}</b>\n\n"
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
        # Short one-line facts are grouped compactly; the paragraphs above are
        # separated by blank lines so they read as distinct blocks.
        meta_lines: list[str] = []
        last = bill.last_stage
        if last is not None:
            when = f" ({last.date.isoformat()})" if last.date else ""
            meta_lines.append(
                f"{ICON['stage']} <b>{esc(lb.stage)}:</b> {esc(last.stage_name)}{when}"
            )
        applicant = lb.applicant_labels.get(s.applicant_type, s.applicant_type.value)
        doc_date = s.document_date.isoformat() if s.document_date else "—"
        meta_lines.append(
            f"{ICON['applicant']} <b>{esc(lb.applicant)}:</b> {esc(applicant)}   "
            f"{ICON['doc_date']} <b>{esc(lb.document_date)}:</b> {esc(doc_date)}"
        )
        if s.prints_considered_jointly:
            meta_lines.append(
                f"{esc(lb.joint_prints)} {esc(', '.join(s.prints_considered_jointly))}"
            )
        if bill.analysis.truncated or bill.analysis.text_source == "metadata_only":
            meta_lines.append(f"{ICON['note']} <i>{esc(lb.partial_text_note)}</i>")
        details.append("\n".join(meta_lines))
        details_block = "\n\n".join(details)

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
                f"#druk{_tag_safe(s.number)}",
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
            f"{ICON['update']} <b>{esc(lb.update_header)} — druk nr {esc(s.number)}</b>\n\n"
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
        if change.closure_detected:
            icon = ICON["passed"] if change.passed else ICON["closed"]
            closure = f"{icon} {esc(lb.process_passed if change.passed else lb.process_closed)}"

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
        tags = f"#{lb.tag_update} #druk{_tag_safe(s.number)} #Sejm{s.term}"

        fixed = [header, badge, closure, links_block, tags]
        text = self._assemble(fixed, flexible=[stages_block, changes_block, summary_block])
        return RenderedMessage(text=text)

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
                f"discovered: {report.discovered} · prefilter hits: {report.prefilter_hits}",
                f"analyzed: {report.analyzed} · failures: {report.analysis_failures}",
                f"published: {report.published} · tracked: {report.tracked} · "
                f"updates: {report.updates} · re-analyzed: {report.reanalyzed}",
                f"tokens in/out: {report.llm_input_tokens}/{report.llm_output_tokens}",
            ]
        )
        errors = ""
        if report.errors:
            errors = "<b>errors</b>\n" + "\n".join(f"• {esc(e)}" for e in report.errors)
        logs = ""
        if log_lines:
            logs = "<b>warnings</b>\n<pre>" + esc("\n".join(log_lines)) + "</pre>"
        text = self._assemble([head, counters, errors], flexible=[logs])
        return RenderedMessage(text=text)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _stage_line(stage: Stage) -> str:
        parts = [stage.date.isoformat() + ":" if stage.date else "", stage.stage_name]
        outcome = stage.decision or stage.position  # SenatePosition reports via `position`
        if outcome:
            parts.append(f"— {outcome}")
        if stage.print_number:
            parts.append(f"(druk {stage.print_number})")
        if stage.committee_code and stage.stage_type == "Referral":
            parts.append(f"[{stage.committee_code}]")
        return esc(" ".join(p for p in parts if p))

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
