"""Routes each `LlmAnalyzer` call to the backend that answers it.

Five independent capabilities (the full analysis, amendments, filed-document digests, joint
comparisons, triage), each its own model by settings — measured separately, prompted separately
where they diverge (`llm_prompts.py`), often different vendors. No single adapter can implement
the whole port honestly, so this one holds five and forwards each method to the one that owns it;
the services never know there is more than one.
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
from lexinform.ports import (
    AmendmentsBackend,
    AnalysisBackend,
    JointBackend,
    SupplementBackend,
    TriageBackend,
)


class HybridAnalyzer:
    def __init__(
        self,
        analysis: AnalysisBackend,
        amendments: AmendmentsBackend,
        supplement: SupplementBackend,
        joint: JointBackend,
        triage: TriageBackend,
    ) -> None:
        self._analysis = analysis
        self._amendments = amendments
        self._supplement = supplement
        self._joint = joint
        self._triage = triage

    def analyze(self, ctx: BillContext) -> AnalysisRecord:
        return self._analysis.analyze(ctx)

    def count_input_tokens(self, ctx: BillContext) -> int | None:
        return self._analysis.count_input_tokens(ctx)

    def summarize_amendments(self, ctx: AmendmentsContext) -> AmendmentsRecord:
        return self._amendments.summarize_amendments(ctx)

    def digest_supplement(self, ctx: SupplementContext) -> SupplementRecord:
        return self._supplement.digest_supplement(ctx)

    def compare_joint(self, ctx: JointContext) -> JointRecord:
        return self._joint.compare_joint(ctx)

    def triage(self, ctx: TriageContext) -> TriageRecord:
        return self._triage.triage(ctx)
