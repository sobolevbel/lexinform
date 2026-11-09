import datetime as dt
import hashlib
from contextlib import closing

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import TextDocument
from lexinform.models.candidate_trace import AmendmentPartGap
from lexinform.services.document_history import DocumentSourceRead, record_source_read
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway
from tests.unit.test_document_candidates import BILL
from tests.unit.test_physical_extraction import archive, docx, extractor

FIRST = "https://api.test/first.docx"
SECOND = "https://api.test/second.docx"
PROPOSAL = "https://api.test/proposal.zip"
UNRELATED = "https://api.test/unrelated.zip"
NOW = dt.datetime(2026, 11, 9, tzinfo=dt.UTC)


@pytest.mark.parametrize("retain_requested", [False, True])
def test_compact_wait_writer_retains_only_the_actual_declared_body(retain_requested: bool) -> None:
    body = docx(BILL + "Art. 2. Przepis stosuje się w postępowaniu. " * 12)
    amendment = archive({"pismo.docx": docx("Pismo przewodnie")})
    gateway = FakeSejmGateway(
        files={FIRST: body, SECOND: body, PROPOSAL: amendment, UNRELATED: amendment}
    )
    loader = TextLoader({"api.test": gateway.download}, extractor(), max_bytes=100_000)
    loader.read(UNRELATED)
    document = TextDocument(
        url=FIRST,
        kind="rcl",
        compare_versions=True,
        version_basis=("v" * 4_096,),
        alternatives=(TextDocument(url=SECOND, kind="rcl", amendment_urls=(PROPOSAL,)),),
    )

    resolved, file = loader.select_document(document)
    source = loader.capture_source_read(
        document, resolved, file, record_requested_document=retain_requested
    )

    assert resolved == document and file.selection.state == "waiting" and file.text is None
    assert isinstance(source, DocumentSourceRead)
    trace = source.candidate_trace
    assert trace is not None, "an admitted waiting comparison retains its compact trace"
    assert trace.coverage == "omitted_over_limit" and trace.omitted_dispositions == 2
    assert trace.requested_document is None and not trace.dispositions
    sha = hashlib.sha256(amendment).hexdigest()
    assert trace.compact_version_part_gaps == (
        AmendmentPartGap(
            phase="version_comparison",
            index=1,
            amendment_index=0,
            reason="returned_no_text",
            file_sha256=sha,
        ),
    )
    assert [(read.requested_url, read.sha256) for read in source.reads if read.sha256 == sha] == [
        (PROPOSAL, sha)
    ]
    assert source.document == document

    without_read = TextLoader({"api.test": gateway.download}, extractor(), max_bytes=100_000)
    without_read.read(UNRELATED)
    fallback = without_read.capture_source_read(
        document, resolved, file, record_requested_document=retain_requested
    )
    assert isinstance(fallback, DocumentSourceRead)
    assert fallback.candidate_trace == trace.model_copy(update={"compact_version_part_gaps": ()})
    assert fallback.selection == source.selection
    assert not fallback.reads

    another_document = document.model_copy(update={"version_basis": ("another edition",)})
    foreign = loader.capture_source_read(
        another_document, another_document, file, record_requested_document=retain_requested
    )
    assert isinstance(foreign, DocumentSourceRead)
    assert (
        foreign.candidate_trace == fallback.candidate_trace
        and foreign.selection == source.selection
    )
    assert PROPOSAL not in {read.requested_url for read in foreign.reads}

    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        with repo.atomic():
            identity = record_source_read(
                repo, source, term=10, number="RCL/123", purpose="analysis", recorded_at=NOW
            )
        assert identity is not None, "the admitted source has an observation identity"
        assert repo.list_candidate_disposition_traces(identity) == [trace]
        dump = repo.dump()
        with repo.atomic():
            assert (
                record_source_read(
                    repo, source, term=10, number="RCL/123", purpose="analysis", recorded_at=NOW
                )
                == identity
            )
        assert repo.dump() == dump
        repo.restore(dump)
        assert repo.dump() == dump
