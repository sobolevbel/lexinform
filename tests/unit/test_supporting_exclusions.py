from contextlib import closing
from dataclasses import replace

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    CandidateDisposition,
    CandidateDispositionTrace,
    DocumentPart,
    DocumentSelection,
    TextDocument,
)
from lexinform.models.candidate_trace import (
    MAX_SUPPORTING_EXCLUSIONS,
    SupportingPartExclusion,
    SupportingPartExclusions,
)
from lexinform.models.document_history import DocumentSelectionRecord, SourceObservation
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.unit.test_input_receipts import NOW
from tests.unit.test_required_part_writer import BODY, MAIN, REQUIRED

REGULATION = "Projekt rozporządzenia\n" + ("Treść projektu. " * 25)


@pytest.mark.parametrize("compare", [True, False])
def test_exclusions_keep_actual_roles_and_a_bounded_ordered_prefix(compare: bool) -> None:
    support = tuple(f"{REQUIRED}/{index}" for index in range(MAX_SUPPORTING_EXCLUSIONS + 2))
    requests: list[str] = []

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        requests.append(url)
        if url == MAIN:
            return BODY.encode()
        if url == support[1]:
            raise FileNotFoundError("private message must not enter typed trace")
        return REGULATION.encode() if url == support[0] else b"empty"

    loader = TextLoader(
        {"source.test": download},
        FakeTextExtractor(
            by_content={BODY.encode(): BODY, REGULATION.encode(): REGULATION, b"empty": ""}
        ),
        max_bytes=100000,
    )
    document = TextDocument(url=MAIN, kind="print", extra_urls=support, compare_versions=compare)

    _, selected = loader.select_document(document)

    assert requests == [MAIN, *support]
    trace = selected.candidate_trace
    assert trace is not None and trace.supporting_exclusions is not None
    evidence = trace.supporting_exclusions
    assert [item.extra_index for item in evidence.rows] == list(range(MAX_SUPPORTING_EXCLUSIONS))
    assert [item.reason for item in evidence.rows[:3]] == [
        "draft_regulation",
        "read_error",
        "returned_no_text",
    ]
    assert evidence.omitted == 2 and evidence.rows[1].file_sha256 is None
    assert "private message" not in trace.model_dump_json()
    assert CandidateDispositionTrace.model_validate_json(trace.model_dump_json()) == trace
    direct = loader.read_document(document)
    assert direct == replace(selected, candidate_trace=None, selection=direct.selection)


def test_excluded_support_requires_a_position_in_the_selected_candidate() -> None:
    document = TextDocument(url=MAIN, kind="print", extra_urls=(REQUIRED,))

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        return BODY.encode() if url == MAIN else b"empty"

    loader = TextLoader(
        {"source.test": download},
        FakeTextExtractor(by_content={BODY.encode(): BODY, b"empty": ""}),
        max_bytes=100000,
    )
    _, file = loader.select_document(document)
    trace = file.candidate_trace
    assert trace is not None and trace.supporting_exclusions is not None
    malformed = trace.model_dump()
    malformed["supporting_exclusions"]["rows"][0]["extra_index"] = 1

    with pytest.raises(ValueError, match="extra URL namespace"):
        CandidateDispositionTrace.model_validate(malformed)


def test_duplicate_support_requires_the_exact_retained_part_after_restore() -> None:
    duplicate = REQUIRED + "-copy"
    document = TextDocument(url=MAIN, kind="print", extra_urls=(REQUIRED, duplicate))
    old = CandidateDispositionTrace.from_decisions(
        document,
        (
            CandidateDisposition(
                phase="fallback", index=0, outcome="selected", reason="first_acceptable_candidate"
            ),
        ),
        result="selected",
        reason="first_acceptable_candidate",
        selection_state="unverified",
    )
    source_sha = "b" * 64
    selection = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            state="unverified",
            parts=(
                DocumentPart(url=MAIN, sha256="a" * 64),
                DocumentPart(url=REQUIRED, sha256=source_sha, text_sha256="c" * 64),
            ),
        )
    )
    observation = SourceObservation(
        term=10,
        number="3039",
        purpose="analysis",
        document=document,
        selection_id=selection.selection_id,
        recorded_at=NOW,
    )
    row = SupportingPartExclusion(
        phase="fallback",
        index=0,
        extra_index=1,
        reason="duplicate_standalone",
        file_sha256=source_sha,
        duplicate_of_part=1,
        duplicate_text_sha256="c" * 64,
    )
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        repo.save_source_observation(selection, observation)
        repo.save_candidate_disposition_trace(observation.observation_id, old)
        before = repo.dump()
        wrong = row.model_copy(update={"duplicate_of_part": 0})
        invalid = CandidateDispositionTrace.model_validate(
            {**old.model_dump(), "supporting_exclusions": SupportingPartExclusions(rows=(wrong,))}
        )
        with pytest.raises(ValueError, match="duplicate_support_parent_mismatch"):
            repo.save_candidate_disposition_trace(observation.observation_id, invalid)
        assert repo.dump() == before
        trace = CandidateDispositionTrace.model_validate(
            {**old.model_dump(), "supporting_exclusions": SupportingPartExclusions(rows=(row,))}
        )
        repo.save_candidate_disposition_trace(observation.observation_id, trace)
        sql = repo.dump()
        repo.restore(sql)
        assert repo.dump() == sql
        assert repo.get_candidate_disposition_trace(trace.trace_id) == trace
        assert repo.get_candidate_disposition_trace(old.trace_id) == old

    with pytest.raises(ValueError, match="requires its body and retained part"):
        SupportingPartExclusion.model_validate(row.model_copy(update={"file_sha256": None}))
