import hashlib
from dataclasses import replace
from typing import Literal

import pytest

from lexinform.errors import SejmApiUnavailableError
from lexinform.models import TextDocument
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_required_part_writer import APPENDIX, BODY, MAIN

ANNEX = "https://source.test/annex"
ANNEX_BYTES = b"annex"


@pytest.mark.parametrize("compare", [False, True])
@pytest.mark.parametrize(
    ("annex", "reason", "state", "kind", "oversize"),
    [
        (None, "no_returned_file", "waiting", None, False),
        ("", "download_size_limit", "waiting", None, True),
        ("", "returned_no_text", "waiting", None, False),
        (APPENDIX, "non_annex_appendix", "ambiguous", "regulation", False),
    ],
)
def test_native_annex_block_retains_actual_gap_without_changing_the_selection(
    compare: bool,
    annex: str | None,
    reason: str,
    state: Literal["waiting", "ambiguous"],
    kind: str | None,
    oversize: bool,
) -> None:
    inputs = {MAIN: BODY.encode()}
    if annex is not None:
        inputs[ANNEX] = ANNEX_BYTES * 100000 if oversize else ANNEX_BYTES
    gateway = FakeSejmGateway(files=inputs)
    extractor = FakeTextExtractor(
        by_content={BODY.encode(): BODY, ANNEX_BYTES: annex or ""}, page_count=0
    )
    loader = TextLoader({"source.test": gateway.download}, extractor, max_bytes=100000)
    document = TextDocument(url=MAIN, kind="rcl", normative_urls=(ANNEX,), compare_versions=compare)

    _, selected = loader.select_document(document)

    trace = selected.candidate_trace
    assert trace is not None and trace.normative_annex_gap is not None, (
        "the native blocked composition has its exact outer parent"
    )
    gap = trace.normative_annex_gap
    assert (gap.index, gap.normative_index, gap.phase) == (
        0,
        0,
        "version_comparison" if compare else "fallback",
    )
    assert (gap.reason, gap.kind, selected.selection.state) == (reason, kind, state)
    assert gap.file_sha256 == (
        hashlib.sha256(ANNEX_BYTES).hexdigest() if annex is not None and not oversize else None
    )
    direct = loader.read_document(document)
    assert direct.candidate_trace is None
    assert direct == replace(selected, candidate_trace=None, selection=direct.selection)
    assert direct.selection.rejected == selected.selection.rejected


def test_normative_annex_outage_still_propagates() -> None:
    failure = SejmApiUnavailableError("download", "technical outage")

    def download(url: str, *, max_bytes: int | None = None) -> bytes:
        if url == ANNEX:
            raise failure
        return BODY.encode()

    loader = TextLoader(
        {"source.test": download}, FakeTextExtractor(BODY, page_count=0), max_bytes=100000
    )

    with pytest.raises(SejmApiUnavailableError) as raised:
        loader.select_document(TextDocument(url=MAIN, kind="rcl", normative_urls=(ANNEX,)))

    assert raised.value is failure


def test_normative_gap_does_not_displace_an_omitted_outer_trace() -> None:
    gateway = FakeSejmGateway(files={MAIN: BODY.encode()})
    loader = TextLoader(
        {"source.test": gateway.download}, FakeTextExtractor(BODY, page_count=0), max_bytes=100000
    )
    document = TextDocument(
        url=MAIN, kind="rcl", normative_urls=(ANNEX,), version_basis=("x" * 5000,)
    )

    _, selected = loader.select_document(document)

    trace = selected.candidate_trace
    assert trace is not None, "the previous outer summary survives optional gap capture"
    assert trace.coverage == "omitted_over_limit"
    assert trace.normative_annex_gap is None
    assert trace.selection_state == "waiting"
