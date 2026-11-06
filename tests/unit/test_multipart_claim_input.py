import hashlib
from contextlib import closing

import pytest
from tools.replay_primary_claims import technical_answer

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import AnalysisQuestion, EvidenceCitation, PreparedTextSegment, ReviewDocument
from lexinform.models.input_receipts import input_map_sha256
from lexinform.primary_claim_input import _validate_key_change_input, validate_key_change_input
from lexinform.primary_claims import KeyChangeEvidence
from tests.unit.test_rcl_archive_input_receipt import offered


@pytest.mark.parametrize("control", ["exact", "wrong_member", "cross_part"])
def test_only_verified_archive_main_binding_enables_existing_literal_map(
    control: str,
) -> None:
    with closing(SqliteBillRepository(":memory:")) as repo:
        repo.migrate()
        intent, receipt = offered(repo)
        selected = repo.get_document_selection(receipt.source_selection_id)
    assert selected is not None, "the native fixture stores the exact source selection"
    part = selected.selection.parts[0]
    question = intent.request.question
    assert isinstance(question, AnalysisQuestion), "fixture queues a primary question"
    text = question.ctx.text
    start = text.index("Art. 1.")
    quote = text[start : start + 35].rstrip()
    document = ReviewDocument(
        identity=part.url,
        url=part.url,
        role="project_text",
        text=text,
        sha256=part.text_sha256,
        normalized_sha256=part.text_sha256,
        file_sha256="0" * 64 if control == "wrong_member" else part.sha256,
    )
    initial = technical_answer(text)
    record = initial.model_copy(
        update={
            "source_kind": "rcl",
            "source_url": part.url,
            "request_sha256": intent.request.payload_sha256,
        }
    )
    evidence = KeyChangeEvidence(
        0,
        hashlib.sha256(record.analysis.key_changes[0].encode()).hexdigest(),
        document.sha256,
        EvidenceCitation(document_id=part.url, quote=quote, locator="Art. 1"),
    )

    if control == "cross_part":
        cut = start + len(quote) - 1
        segments = (
            PreparedTextSegment(
                start=0, end=cut, origin="source", part_index=0, source_start=0, source_end=cut
            ),
            PreparedTextSegment(
                start=cut, end=cut + 1, origin="source", part_index=1, source_start=0, source_end=1
            ),
            PreparedTextSegment(
                start=cut + 1, end=len(text), origin="generated", system_origin="input_transform"
            ),
        )
        receipt = receipt.model_copy(
            update={"segments": segments, "full_map_sha256": input_map_sha256(segments)}
        )
        intent = intent.model_copy(
            update={"meta": intent.meta.model_copy(update={"prepared_input_receipt": receipt})}
        )

    default = validate_key_change_input(
        record, evidence, (document,), intent=intent, source_selection=selected
    )
    checked = _validate_key_change_input(
        record, evidence, (document,), intent=intent, verified_multipart_selection=selected
    )

    assert default.status == "unknown" and default.reason == "source_binding_unavailable"
    assert (
        checked.status
        == {"exact": "retained", "wrong_member": "unknown", "cross_part": "omitted"}[control]
    )
    assert checked.input_span == ((start, start + len(quote)) if control == "exact" else None)
