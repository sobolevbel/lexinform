import hashlib
import json

from lexinform.adapters.review_prompts import review_prompt
from lexinform.consultation_batches import plan_consultation_parts
from lexinform.models import ReviewContext, ReviewDocument
from lexinform.models.reviews import ConsultationRow


def _context() -> ReviewContext:
    row = "1\tWniosek\tUwaga uwzględniona."
    paragraph = "Projektodawca odpowiedział.\fZobacz pkt. 1 raportu."
    text = f"{row}\n{paragraph}"
    digest = hashlib.sha256(text.encode()).hexdigest()
    document = ReviewDocument(
        identity="rcl/1",
        url="https://rcl.test/1",
        role="response",
        text=text,
        sha256=digest,
        normalized_sha256=digest,
        consultation_rows=(
            ConsultationRow(
                locator="word-xml:word/document.xml:0/1/0",
                label="1",
                start=0,
                end=len(row),
            ),
            ConsultationRow(
                locator="word-xml:word/document.xml:0/1/1",
                kind="paragraph",
                start=len(row) + 1,
                end=len(text),
            ),
        ),
    )
    return ReviewContext(
        number="RCL/1",
        title="Projekt",
        purpose="consultation_review",
        documents=(document,),
    )


def test_consultation_prompt_packs_complete_paragraphs_without_changing_legacy_rows() -> None:
    context = _context()
    document = context.documents[0]
    row, paragraph = document.consultation_rows
    row_segment = {"locator": row.locator, "label": "1", "text": document.text[: row.end]}
    paragraph_text = document.text[paragraph.start :]
    expected = [
        row_segment,
        {"text": "\n"},
        {"locator": paragraph.locator, "label": None, "text": paragraph_text, "kind": "paragraph"},
    ]

    assert json.loads(review_prompt(context))["documents"][0]["segments"] == expected
    for version in ("consultation-review-v3", "consultation-review-v4", "consultation-review-v5"):
        assert json.loads(review_prompt(context, version=version))["documents"][0]["segments"] == [
            row_segment,
            {"text": f"\n{paragraph_text}"},
        ]
    legacy = json.loads(review_prompt(context, version="consultation-review-v2"))["documents"][0]
    assert legacy["text"] == document.text
    assert legacy["consultation_rows"] == [
        {"locator": row.locator, "label": "1", "start": row.start, "end": row.end}
    ]


def test_consultation_planner_keeps_a_paragraph_intact_without_following_table_references() -> None:
    context = _context()
    source = context.documents[0]
    paragraph = source.consultation_rows[1]

    parts = plan_consultation_parts(context, 10, len, input_limit=4170)

    assert len(parts) == 2
    for part in parts:
        (document,) = part.documents
        assert len(document.consultation_rows) == 1
    document = parts[1].documents[0]
    (span,) = document.consultation_rows
    assert span.kind == "paragraph"
    assert document.text[span.start : span.end] == source.text[paragraph.start : paragraph.end]
