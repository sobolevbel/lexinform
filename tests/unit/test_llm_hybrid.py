"""HybridAnalyzer routes each `LlmAnalyzer` call to the backend that owns it."""

from dataclasses import dataclass, field
from typing import Any

from lexinform.adapters.llm_hybrid import HybridAnalyzer
from lexinform.models import (
    AmendmentsContext,
    ApplicantType,
    BillContext,
    JointBillDescription,
    JointContext,
    SupplementContext,
    TriageContext,
)


@dataclass
class _Calls:
    seen: list[str] = field(default_factory=list)


class _Recording:
    """A stand-in that records which of its methods got called, whatever the arguments."""

    def __init__(self, calls: _Calls, name: str) -> None:
        self._calls = calls
        self._name = name

    def __getattr__(self, method: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self._calls.seen.append(f"{self._name}.{method}")

        return record


_BILL_CTX = BillContext(
    number="3039",
    title="Projekt",
    description=None,
    document_date=None,
    applicant_type=ApplicantType.DEPUTIES,
    text="Art. 1.",
    truncated=False,
    text_source="pdf",
)

_TRIAGE_CTX = TriageContext(
    number="3039",
    title="Projekt",
    description=None,
    applicant_type=ApplicantType.DEPUTIES,
    excerpts="...",
    text_chars=100,
)

_AMENDMENTS_CTX = AmendmentsContext(
    number="3039",
    title="Projekt",
    source_kind="senate_amendments",
    text="Poprawka 1.",
    truncated=False,
    previous_summary="Опис.",
)

_SUPPLEMENT_CTX = SupplementContext(
    number="3039",
    title="Projekt",
    document_title="Stanowisko",
    source_kind="government_position",
    text="Tekst.",
    truncated=False,
    previous_summary="Опис.",
)

_JOINT_CTX = JointContext(
    subject=JointBillDescription(
        number="3039", title="Projekt", applicant_type=ApplicantType.DEPUTIES, summary="Опис."
    ),
    others=[],
)


def _hybrid(calls: _Calls) -> HybridAnalyzer:
    return HybridAnalyzer(
        analysis=_Recording(calls, "analysis"),
        amendments=_Recording(calls, "amendments"),
        supplement=_Recording(calls, "supplement"),
        joint=_Recording(calls, "joint"),
        triage=_Recording(calls, "triage"),
    )


def test_analyze_and_count_input_tokens_go_to_the_analysis_backend() -> None:
    calls = _Calls()
    hybrid = _hybrid(calls)

    hybrid.analyze(_BILL_CTX)
    hybrid.count_input_tokens(_BILL_CTX)

    assert calls.seen == ["analysis.analyze", "analysis.count_input_tokens"]


def test_each_secondary_capability_goes_to_its_own_backend() -> None:
    calls = _Calls()
    hybrid = _hybrid(calls)

    hybrid.summarize_amendments(_AMENDMENTS_CTX)
    hybrid.digest_supplement(_SUPPLEMENT_CTX)
    hybrid.compare_joint(_JOINT_CTX)
    hybrid.triage(_TRIAGE_CTX)

    assert calls.seen == [
        "amendments.summarize_amendments",
        "supplement.digest_supplement",
        "joint.compare_joint",
        "triage.triage",
    ]
