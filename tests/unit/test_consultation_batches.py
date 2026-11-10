import hashlib
import json

import pytest
import tiktoken

from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.consultation_batches import plan_consultation_parts
from lexinform.models import ReviewContext, ReviewDocument
from lexinform.models.batch import BatchRequest, DocumentReviewQuestion
from lexinform.models.reviews import ConsultationRow


def test_consultation_parts_keep_cross_partition_references_and_exact_source_rows() -> None:
    rows = (
        "1\tUwaga\tOdpowiedź " + "odmowa " * 80,
        "2\tInny wniosek\tOdpowiedź " + "odmowa " * 90,
        "3\tWniosek\tZobacz pkt. 1 raportu.",
    )
    text = "\n".join(rows)
    digest = hashlib.sha256(text.encode()).hexdigest()
    offsets = (0, len(rows[0]) + 1, len(rows[0]) + len(rows[1]) + 2)
    source = ReviewDocument(
        identity="rcl/42",
        url="https://rcl.test/42",
        role="response",
        text=text,
        sha256=digest,
        normalized_sha256=digest,
        consultation_rows=tuple(
            ConsultationRow(
                locator=f"word-xml:word/document.xml:0/1/{index}",
                label=str(index),
                start=start,
                end=start + len(row),
            )
            for index, (start, row) in enumerate(zip(offsets, rows, strict=True), 1)
        ),
    )
    root = ReviewContext(
        number="RCL/1",
        title="Projekt",
        purpose="consultation_review",
        documents=(source,),
    )
    tokenizer = tiktoken.get_encoding("o200k_base")

    parts = plan_consultation_parts(
        root, 10, lambda text: len(tokenizer.encode_ordinary(text)), input_limit=4260
    )

    assert len(parts) > 1
    assert len({part.memo_key(10) for part in parts}) == len(parts)
    assert {
        row.label for part in parts for doc in part.documents for row in doc.consultation_rows
    } == {"1", "2", "3"}
    for part in parts:
        for doc in part.documents:
            labels = {row.label for row in doc.consultation_rows}
            if "3" in labels:
                assert "1" in labels
            for row in doc.consultation_rows:
                original = next(
                    item for item in source.consultation_rows if item.locator == row.locator
                )
                assert doc.text[row.start : row.end] == text[original.start : original.end]

    analyzer = OpenAiAnalyzer(
        lambda: pytest.fail("a local preflight must not call the API"), model="gpt-6.1-sol"
    )
    estimates = []
    for part in parts:
        request = analyzer.prepare_request(
            BatchRequest(
                custom_id=part.memo_key(10),
                term=10,
                number=part.number,
                question=DocumentReviewQuestion(kind="consultation_review", ctx=part),
            )
        )
        assert request.payload_json is not None, "the preflight freezes a provider payload"
        assert json.loads(request.payload_json)["body"]["max_output_tokens"] == 5000
        estimates.append(request.estimated_cost_usd)
    assert 0 < sum(estimates) < 2.0
