import datetime as dt
from collections.abc import Iterable
from typing import Annotated, Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, Field

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import (
    DocumentDownloadDiagnostics,
    ReportField,
    RunMode,
    RunReport,
    TokenUsage,
)
from tests.formatting import assert_telegram_html

NOW = dt.datetime(2026, 11, 14, tzinfo=dt.UTC)
COUNTS = st.dictionaries(
    st.from_regex(r"future_[a-z]{1,10}", fullmatch=True),
    st.integers(min_value=0, max_value=50),
    max_size=8,
)
EMPTY_VALUES = st.recursive(
    st.sampled_from([None, False, 0, 0.0, "", " \n"]),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=5), children, max_size=3),
    ),
    max_leaves=10,
)


class ExtendedReport(RunReport):
    future_values: Annotated[object, ReportField("new measurements")] = None


class CountGroup(BaseModel):
    version: Annotated[Literal[1], ReportField(role="metadata")] = 1
    observed_at: Annotated[dt.datetime, ReportField(role="metadata")] = NOW
    waiting: Annotated[int, ReportField(role="context")] = 3
    changed: int = 0


class ContextReport(RunReport):
    future_header: Annotated[str, ReportField(role="metadata")] = "do not display"
    future_context: Annotated[int, ReportField(role="context")] = 12
    future_group: CountGroup = Field(default_factory=CountGroup)


def render(report: RunReport) -> str:
    return MessageFormatter("ru").run_report(report, []).text


def counter_report_type(names: Iterable[str]) -> type[RunReport]:
    names = tuple(names)
    return type(
        "FutureReport",
        (RunReport,),
        {"__annotations__": dict.fromkeys(names, int), **dict.fromkeys(names, 0)},
    )


@given(COUNTS)
def test_added_and_removed_counter_fields_need_no_formatter_or_activity_changes(
    counts: dict[str, int],
) -> None:
    extended = counter_report_type(counts)
    report = extended(started_at=NOW, since=NOW, mode=RunMode.RUN, **counts)
    stored = report.model_dump_json()

    text = render(report)

    assert report.is_empty == (not any(counts.values()))
    assert ("<b>other</b>" in text) == (not report.is_empty)
    for name, count in counts.items():
        assert (f"{name}: {count}" in text) == bool(count)
    assert report.model_dump_json() == stored
    assert extended.model_validate_json(stored).is_empty == report.is_empty
    reduced = counter_report_type(name for name, count in counts.items() if count)
    reduced_report = reduced(
        started_at=NOW,
        since=NOW,
        mode=RunMode.RUN,
        **{name: count for name, count in counts.items() if count},
    )
    assert render(reduced_report) == text
    assert reduced_report.is_empty == report.is_empty


@given(EMPTY_VALUES)
def test_nested_empty_values_never_create_a_result_or_section(value: object) -> None:
    report = ExtendedReport(started_at=NOW, since=NOW, mode=RunMode.RUN, future_values=value)

    assert bool(report.is_empty)
    assert "new measurements" not in render(report)

    report.future_values = {"empty": value, "changed": 7}

    assert not report.is_empty
    text = render(report)
    assert "new measurements" in text
    assert "future_values.changed: 7" in text
    assert "future_values.empty" not in text


def test_nested_names_are_literal_and_html_escaped() -> None:
    report = ExtendedReport(
        started_at=NOW,
        since=NOW,
        mode=RunMode.RUN,
        future_values={"<source>{key}": {"zero": 0, "sent": 2}},
    )

    text = render(report)

    assert "future_values.&lt;source&gt;{key}.sent: 2" in text
    assert "zero" not in text
    assert_telegram_html(text)


def test_declared_context_and_metadata_do_not_make_a_run_active() -> None:
    report = ContextReport(started_at=NOW, since=NOW, mode=RunMode.RUN)

    text = render(report)

    assert bool(report.is_empty)
    assert "future_context: 12" in text and "future_group.waiting: 3" in text
    assert "future_header" not in text and "do not display" not in text
    assert "future_group.version" not in text and "future_group.observed_at" not in text

    report.future_group.changed = 1

    assert not report.is_empty
    assert "future_group.changed: 1" in render(report)


def test_zero_attention_and_usage_do_not_make_a_quiet_run_active() -> None:
    report = RunReport(
        started_at=NOW,
        since=NOW,
        mode=RunMode.RUN,
        tracked=12,
        attention={"continuation_messages": 0, "another_counter": 0},
        llm_usage={"unused_model": TokenUsage()},
        notes=["", "  "],
    )

    text = render(report)

    assert report.is_empty
    assert "tracked: 12" in text
    assert all(
        label not in text for label in ("attention: delivery", "unused_model", "tokens", "notes")
    )
    assert report.attention == {"continuation_messages": 0, "another_counter": 0}


@pytest.mark.parametrize("parts,events", [(0, 0), (1, 0), (0, 2), (1, 2)])
def test_optional_section_follows_its_remaining_counters(parts: int, events: int) -> None:
    report = RunReport(
        started_at=NOW,
        since=NOW,
        mode=RunMode.RUN,
        digest_reader_parts=parts,
        digest_events_covered=events,
    )

    text = render(report)

    assert report.is_empty == (not (parts or events))
    assert ("digest delivery" in text) == bool(parts or events)
    assert (f"reader parts: {parts}" in text) == bool(parts)
    assert (f"covered events: {events}" in text) == bool(events)


def test_read_reports_filter_zero_counters_without_inventing_execution_results() -> None:
    report = RunReport(
        started_at=NOW,
        since=NOW,
        mode=RunMode.CLI_READS,
        cli_read_operation="scan",
        document_download_diagnostics=DocumentDownloadDiagnostics(cache_hits=1),
        published=7,
    )

    text = render(report)

    assert not report.is_empty
    assert "cache hits: 1" in text
    assert ": 0" not in text and "new cards" not in text
    assert "unmeasured" in text
