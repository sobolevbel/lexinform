import pytest

from lexinform.input_receipts import rcl_archive_self_amendment_url
from lexinform.models import TextDocument
from lexinform.models.input_receipts import (
    ArchiveInputPart,
    LooseRclInputPart,
    RclArchiveSelfAmendmentInputReceipt,
    input_map_sha256,
)
from lexinform.models.prepared_input import PreparedTextSegment


def test_hybrid_receipt_keeps_one_distinct_final_amendment_span() -> None:
    segments = (
        PreparedTextSegment(
            start=0, end=3, origin="source", part_index=0, source_start=0, source_end=3
        ),
        PreparedTextSegment(start=3, end=5, origin="generated", system_origin="input_transform"),
        PreparedTextSegment(
            start=5, end=8, origin="source", part_index=2, source_start=0, source_end=3
        ),
    )
    receipt = RclArchiveSelfAmendmentInputReceipt(
        source_selection_id="selection-v1:" + "1" * 64,
        source_observation_id="source-observation-v1:" + "2" * 64,
        extraction_id="extraction-v1:" + "3" * 64,
        source_parts=(
            ArchiveInputPart(role="bill", chars=3, text_sha256="a" * 64, physical_index=2),
            ArchiveInputPart(
                role="normative_annex", chars=3, text_sha256="b" * 64, physical_index=3
            ),
            LooseRclInputPart(
                role="self_amendment",
                chars=3,
                text_sha256="c" * 64,
                extraction_id="extraction-v1:" + "4" * 64,
            ),
        ),
        context_sha256="d" * 64,
        request_sha256="e" * 64,
        text_sha256="f" * 64,
        text_chars=8,
        full_map_sha256=input_map_sha256(segments),
        segments=segments,
    )

    assert (
        RclArchiveSelfAmendmentInputReceipt.model_validate_json(receipt.model_dump_json())
        == receipt
    )
    wrong_final = segments[-1].model_copy(update={"part_index": 1})
    wrong_map = (*segments[:-1], wrong_final)
    with pytest.raises(ValueError, match="complete final amendment"):
        RclArchiveSelfAmendmentInputReceipt.model_validate(
            {
                **receipt.model_dump(),
                "segments": wrong_map,
                "full_map_sha256": input_map_sha256(wrong_map),
            }
        )
    wrong_amendment = receipt.amendment_part.model_copy(
        update={"extraction_id": receipt.extraction_id}
    )
    with pytest.raises(ValueError, match="one distinct final loose"):
        RclArchiveSelfAmendmentInputReceipt.model_validate(
            {**receipt.model_dump(), "source_parts": (*receipt.archive_parts, wrong_amendment)}
        )


def test_hybrid_scope_accepts_only_the_exact_recorded_amendment_alternative() -> None:
    archive_url = "https://api.test/project.zip"
    amendment_url = "https://api.test/proposal.pdf"
    alternate = TextDocument(kind="rcl", url=amendment_url)
    document = TextDocument(
        kind="rcl", url=archive_url, amendment_urls=(amendment_url,), alternatives=(alternate,)
    )

    assert rcl_archive_self_amendment_url(document) == amendment_url
    for field, value in (
        ("required_urls", (amendment_url,)),
        ("amendment_urls", (archive_url,)),
        ("alternatives", (alternate.model_copy(update={"url": "https://api.test/other.pdf"}),)),
        ("alternatives", (alternate.model_copy(update={"official_link_conflict": True}),)),
    ):
        assert rcl_archive_self_amendment_url(document.model_copy(update={field: value})) is None
