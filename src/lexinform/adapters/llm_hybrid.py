"""Routes each `LlmAnalyzer` call to the model that answers it.

The full per-bill analysis and everything else can be different models entirely — measured
separately, prompted separately (`llm_prompts.py`), often different vendors — so no single
adapter can implement the whole port honestly. This one holds two `LlmAnalyzer`s that each
already do, and forwards each method to the one that owns it; the services never know there are
two.
"""

from lexinform.models import (
    AmendmentsContext,
    AmendmentsRecord,
    AnalysisRecord,
    BillContext,
    JointContext,
    JointRecord,
    SupplementContext,
    SupplementRecord,
    TriageContext,
    TriageRecord,
)
from lexinform.ports import AnalysisBackend, SecondaryBackend


class HybridAnalyzer:
    """`analyze` and `count_input_tokens` go to `analysis`; triage, amendments, filed-document
    digests and joint comparisons go to `secondary`."""

    def __init__(self, analysis: AnalysisBackend, secondary: SecondaryBackend) -> None:
        self._analysis = analysis
        self._secondary = secondary

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        return self._analysis.analyze(ctx)

    def count_input_tokens(self, ctx: BillContext) -> int | None:
        return self._analysis.count_input_tokens(ctx)

    def triage(self, ctx: TriageContext) -> TriageRecord:
        return self._secondary.triage(ctx)

    def summarize_amendments(self, ctx: AmendmentsContext) -> AmendmentsRecord:
        return self._secondary.summarize_amendments(ctx)

    def digest_supplement(self, ctx: SupplementContext) -> SupplementRecord:
        return self._secondary.digest_supplement(ctx)

    def compare_joint(self, ctx: JointContext) -> JointRecord:
        return self._secondary.compare_joint(ctx)
