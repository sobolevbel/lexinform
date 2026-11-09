from lexinform.document_candidate_observations import CandidateObservations
from lexinform.models.documents import DocumentPart
from lexinform.models.sejm import TextDocument


def test_comparison_projection_preserves_exact_official_and_archive_observations() -> None:
    later = "https://example.test/later.zip"
    earlier = "https://example.test/earlier.zip"
    amendment = "https://example.test/amendment.pdf"
    source = TextDocument(
        url=later,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=earlier, kind="rcl", amendment_urls=(amendment,)),),
    )
    declared = CandidateObservations.from_sources(source)
    primary, older = declared.entries
    physical = DocumentPart(url=later, members=("nested.zip", "bill.docx"))
    rejected = DocumentPart(url=later, members=("nested.zip", "table.docx"))
    older_physical = physical.model_copy(update={"url": earlier})
    first = primary.with_read("USTAWA", (physical,), (physical, rejected))
    second = older.with_read("USTAWA", (older_physical,), (older_physical,))

    observations = CandidateObservations((first, second))
    projection = observations.comparison((1,))

    assert observations.official_sources() == (source, *source.alternatives)
    assert first.candidate.observed_parts == (physical, rejected)
    assert second.official == source.alternatives[0]
    assert projection.entries == (second.candidate,)
