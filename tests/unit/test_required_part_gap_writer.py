import hashlib
from dataclasses import replace

import pytest

from lexinform.errors import AttachmentTooLargeError
from lexinform.models import TextDocument
from lexinform.models.candidate_trace import MAX_CANDIDATE_TRACE_BYTES
from lexinform.services.documents import TextLoader
from tests.fakes import FakeTextExtractor
from tests.unit.test_required_part_writer import APPENDIX, BODY, MAIN, REQUIRED

FIRST = REQUIRED + "/first"
SECOND = REQUIRED + "/second"
LATER = REQUIRED + "/later"
EMPTY = b"returns no text"


def loader_with_gap() -> tuple[TextLoader, list[str]]:
    requests: list[str] = []
    files = {MAIN: BODY.encode(), FIRST: APPENDIX.encode(), SECOND: EMPTY}

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        requests.append(url)
        return files[url]

    return (
        TextLoader(
            {"source.test": download},
            FakeTextExtractor(
                by_content={BODY.encode(): BODY, APPENDIX.encode(): APPENDIX, EMPTY: ""},
                page_count=0,
            ),
            max_bytes=100000,
        ),
        requests,
    )


@pytest.mark.parametrize("compare", [False, True])
def test_terminal_gap_retains_earlier_exclusion_and_never_reads_later_parts(compare: bool) -> None:
    loader, requests = loader_with_gap()
    document = TextDocument(
        url=MAIN,
        kind="print",
        required_urls=(FIRST, SECOND, LATER),
        normative_urls=(LATER,),
        compare_versions=compare,
    )

    _, file = loader.select_document(document)

    assert requests == [MAIN, FIRST, SECOND]
    assert file.selection.state == "waiting"
    trace = file.candidate_trace
    assert trace is not None and trace.required_part_gap is not None, (
        "the blocked composition has an observed required file"
    )
    gap = trace.required_part_gap
    assert gap.phase == ("version_comparison" if compare else "fallback")
    assert (gap.index, gap.required_index, gap.reason, gap.file_sha256) == (
        0,
        1,
        "returned_no_text",
        hashlib.sha256(EMPTY).hexdigest(),
    )
    assert trace.normative_annex_gap is None
    assert trace.required_exclusions is not None, "earlier exclusion remains independently recorded"
    assert [row.required_index for row in trace.required_exclusions.rows] == [0]
    direct = loader.read_document(document)
    assert direct == replace(file, candidate_trace=None, selection=direct.selection)
    assert direct.selection.rejected == file.selection.rejected
    assert requests == [MAIN, FIRST, SECOND]


def test_terminal_gap_is_omitted_before_existing_required_exclusions_at_the_bound() -> None:
    loader, _ = loader_with_gap()
    document = TextDocument(url=MAIN, kind="print", required_urls=(FIRST, SECOND))
    _, original = loader.select_document(document)
    trace = original.candidate_trace
    assert trace is not None and trace.required_part_gap is not None, "fixture observes the gap"
    prior = trace.model_copy(update={"required_part_gap": None})
    padding = MAX_CANDIDATE_TRACE_BYTES - len(prior.model_dump_json(exclude_defaults=True)) - 25
    tight = document.model_copy(update={"version_basis": ("x" * padding,)})

    _, file = loader.select_document(tight)

    result = file.candidate_trace
    assert result is not None and result.required_exclusions == trace.required_exclusions
    assert result.required_part_gap is None
    assert file.selection.state == original.selection.state == "waiting"


def test_required_loader_exception_propagates_without_recording_a_return() -> None:
    failure = FileNotFoundError("required test input absent")

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        if url == SECOND:
            raise failure
        return BODY.encode()

    loader = TextLoader(
        {"source.test": download}, FakeTextExtractor(BODY, page_count=0), max_bytes=100000
    )
    document = TextDocument(url=MAIN, kind="print", required_urls=(SECOND,))

    with pytest.raises(FileNotFoundError) as raised:
        loader.select_document(document)

    assert raised.value is failure


def test_required_body_size_refusal_waits_without_claiming_a_returned_file() -> None:
    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        if url == SECOND:
            raise AttachmentTooLargeError(url, 100000)
        return BODY.encode()

    loader = TextLoader(
        {"source.test": download}, FakeTextExtractor(BODY, page_count=0), max_bytes=100000
    )
    document = TextDocument(url=MAIN, kind="print", required_urls=(SECOND,))

    _, file = loader.select_document(document)

    assert file.selection.state == "waiting"
    assert file.candidate_trace is not None, "the prior waiting decision is still recorded"
    assert file.candidate_trace.required_part_gap is None
