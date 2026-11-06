from dataclasses import replace

import pytest

from lexinform.models import CandidateDispositionTrace, TextDocument
from lexinform.models.candidate_trace import MAX_SUPPORTING_EXCLUSIONS
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
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
