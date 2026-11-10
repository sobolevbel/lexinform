import hashlib
import json
from datetime import date

import pytest
import tiktoken

from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.consultation_batches import (
    CONSULTATION_SYNTHESIS_RESERVE,
    ConsultationInputTooLargeError,
    consultation_archive_fingerprint,
    consultation_synthesis_context,
    plan_consultation_parts,
    prepare_consultation_parts,
)
from lexinform.models import DocumentPart, DocumentReview, ReviewContext, ReviewDocument
from lexinform.models.batch import BatchRequest, DocumentReviewQuestion
from lexinform.models.reviews import (
    ConsultationDraftChange,
    ConsultationRow,
    EvidenceCitation,
    validate_review,
)
from tests.scenario.test_document_reviews import context as review_context
from tests.scenario.test_document_reviews import finding as review_finding


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
    draft = review_context("Art. 1. Nowa wersja projektu.", role="project_text").documents[0]
    revised = root.model_copy(update={"documents": (*root.documents, draft)})
    revised_parts = plan_consultation_parts(
        revised, 10, lambda text: len(tokenizer.encode_ordinary(text)), input_limit=4260
    )
    assert revised_parts == parts
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


def test_consultation_archives_deduplicate_repackaged_reports_without_losing_revisions() -> None:
    def member(name: str, text: str) -> DocumentPart:
        digest = hashlib.sha256(text.encode()).hexdigest()
        return DocumentPart(members=(name,), text=text, sha256=digest, text_sha256=digest)

    report = member("report.docx", "Raport z konsultacji")
    answer = member("answer.docx", "Uwaga została uwzględniona")
    revised = member("answer.docx", "Uwaga nie została uwzględniona")
    renamed = answer.model_copy(update={"members": ("copy.docx",)})
    incomplete = answer.model_copy(update={"losses": ("missing page",)})
    first_key = consultation_archive_fingerprint((report, answer))
    alias_key = consultation_archive_fingerprint((renamed, report))
    revision_key = consultation_archive_fingerprint((report, revised))

    assert first_key and alias_key == first_key and revision_key != first_key
    assert consultation_archive_fingerprint((report, incomplete)) is None
    assert consultation_archive_fingerprint((report, answer, answer)) != first_key

    def source(identity: str, parts: tuple[DocumentPart, ...]) -> ReviewDocument:
        text = "\n\n".join(part.text for part in parts)
        digest = hashlib.sha256(text.encode()).hexdigest()
        return ReviewDocument(
            identity=identity,
            url=f"https://rcl.test/{identity}.zip",
            role="response",
            text=text,
            sha256=digest,
            normalized_sha256=digest,
            consultation_archive_fingerprint=consultation_archive_fingerprint(parts),
        )

    root = review_context().model_copy(
        update={
            "documents": (
                source("first", (report, answer)),
                source("alias", (renamed, report)),
                source("revision", (report, revised)),
            )
        }
    )
    (part,) = plan_consultation_parts(root, 10)

    assert [item.identity for item in part.documents] == ["first", "revision"]
    assert "Uwaga nie została uwzględniona" in part.documents[1].text


def test_consultation_preflight_repacks_before_enqueuing_oversized_requests() -> None:
    text = "\n\n".join(f"{index}. " + "uwaga " * 4_500 for index in range(15))
    root = review_context(text)
    tokenizer = tiktoken.get_encoding("o200k_base")
    attempts: list[int] = []

    def prepare(request: BatchRequest) -> BatchRequest:
        question = request.question
        assert isinstance(question, DocumentReviewQuestion), "this preflight prepares reviews"
        tokens = len(tokenizer.encode_ordinary(question.ctx.text))
        attempts.append(tokens)
        if tokens > 49_000:
            raise ConsultationInputTooLargeError("Serialized request exceeds limit")
        return request

    prepared = prepare_consultation_parts(root, 10, prepare)

    assert attempts[0] > 49_000
    assert all(tokens <= 49_000 for tokens in attempts[1:])
    assert "".join(part.text for part, _ in prepared) == text


def test_synthesis_only_returns_ranked_ids_from_verified_candidates() -> None:
    root = review_context()
    findings = (
        review_finding(),
        review_finding().model_copy(update={"proposal": "Срок", "theme": "Документы"}),
    )
    ctx, originals = consultation_synthesis_context(root, 10, findings)
    response = DocumentReview(
        purpose="consultation_review",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(),
        unresolved=(),
        selected_candidates=("2", "1"),
    )
    result = validate_review(response, ctx)
    assert tuple(originals[key] for key in result.selected_candidates) == findings[::-1]
    with pytest.raises(ValueError, match="distinct known proposals"):
        validate_review(response.model_copy(update={"selected_candidates": ("3",)}), ctx)
    analyzer = OpenAiAnalyzer(lambda: pytest.fail("preflight is offline"), model="gpt-6.1-sol")
    request = analyzer.prepare_request(
        BatchRequest(
            custom_id=ctx.memo_key(10),
            term=10,
            number=ctx.number,
            question=DocumentReviewQuestion(kind="consultation_review", ctx=ctx),
        )
    )
    assert request.payload_json is not None, "the final selection request is frozen"
    body = json.loads(request.payload_json)["body"]
    assert "selected_candidates" in body["text"]["format"]["schema"]["required"]
    assert request.estimated_cost_usd < CONSULTATION_SYNTHESIS_RESERVE


def test_synthesis_checks_only_selected_proposals_against_dated_articles() -> None:
    before = (
        review_context(
            "Art. 3. Wniosek składa się w terminie 14 dni.\nArt. 4. Inny przepis.",
            role="comparison_text",
        )
        .documents[0]
        .model_copy(update={"identity": "before", "draft_date": date(2026, 1, 1)})
    )
    after = (
        review_context(
            "Art. 3. Wniosek składa się w terminie 30 dni.\nArt. 4. Inny przepis.",
            role="project_text",
        )
        .documents[0]
        .model_copy(update={"identity": "after", "draft_date": date(2026, 2, 1)})
    )
    original = review_context()
    root = original.model_copy(
        update={
            "documents": (*original.documents, before, after),
            "comparison_sha256": before.normalized_sha256,
        }
    )
    proposed = review_finding(quote="Pkt 1. Uwaga uwzględniona. Zmiana art. 3.")
    synthesis, _ = consultation_synthesis_context(root, 10, (proposed,))
    old, new = synthesis.documents
    assert "Art. 4" not in old.text and "Art. 4" not in new.text
    change = ConsultationDraftChange(
        candidate_id="1",
        text_change="Срок увеличили с 14 до 30 дней",
        comparison_evidence=EvidenceCitation(
            document_id="before", locator="Art. 3", quote=old.text
        ),
        text_evidence=EvidenceCitation(document_id="after", locator="Art. 3", quote=new.text),
    )
    result = DocumentReview(
        purpose="consultation_review",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(),
        unresolved=(),
        selected_candidates=("1",),
        compared_changes=(change,),
    )
    assert validate_review(result, synthesis).compared_changes == (change,)
    wrong = change.model_copy(
        update={"text_evidence": change.text_evidence.model_copy(update={"quote": old.text})}
    )
    assert not validate_review(
        result.model_copy(update={"compared_changes": (wrong,)}), synthesis
    ).compared_changes
