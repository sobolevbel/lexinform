import datetime as dt
import hashlib
from dataclasses import replace
from typing import cast

import pytest

from lexinform.document_fingerprints import text_digest
from lexinform.models.analysis import Analysis, AnalysisRecord
from lexinform.models.enums import Category
from lexinform.models.reviews import (
    DocumentRole,
    EvidenceCitation,
    ReviewContext,
    ReviewDocument,
    citation_document,
    citation_document_in,
)
from lexinform.primary_claims import (
    AffectedGroupEvidence,
    ClaimEvidenceCheck,
    KeyChangeEvidence,
    ProposalEditionEvidence,
    ProposedChangeEvidence,
    ProposedCommencementEvidence,
    validate_affected_group_evidence,
    validate_key_change_evidence,
    validate_proposed_change_evidence,
    validate_proposed_commencement_evidence,
)

CLAIM = "С 1 января 2027 года срок для студентов — 30 дней."
QUOTE = "Art. 3. Od dnia 1 stycznia 2027 r. termin dla studentów wynosi 30 dni."


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def record() -> AnalysisRecord:
    return AnalysisRecord(
        analysis=Analysis(
            relevant=True,
            score=3,
            category=Category.LEGAL_STAY,
            summary="Предлагается новый срок для студентов.",
            key_changes=[CLAIM, "Заявление подают в электронном виде."],
            practical_impact="Студенты смогут подать заявление в новый срок.",
            confidence=0.9,
            rationale="Меняется срок подачи заявления.",
        ),
        model="gpt-6.1-sol",
        prompt_version="test",
        input_chars=len(QUOTE),
        truncated=False,
        text_source="pdf",
        text_sha256=text_digest(QUOTE),
        created_at=dt.datetime(2026, 11, 4, tzinfo=dt.UTC),
    )


def source(text: str = QUOTE, *, role: DocumentRole = "project_text") -> ReviewDocument:
    return ReviewDocument(
        identity="project/one",
        url="https://example.test/project.pdf",
        role=role,
        text=text,
        sha256=digest(text),
        normalized_sha256=text_digest(text),
        file_sha256="f" * 64,
    )


def evidence(document: ReviewDocument) -> KeyChangeEvidence:
    return KeyChangeEvidence(
        index=0,
        claim_text_sha256=digest(CLAIM),
        source_text_sha256=document.sha256,
        citation=EvidenceCitation(document_id=document.identity, locator="Art. 3", quote=QUOTE),
    )


def test_exact_quote_is_only_observed_text_and_preserves_current_record_json() -> None:
    answer = record()
    document = source("Projekt\n" + QUOTE + "\nUzasadnienie")
    saved_answer = answer.model_dump_json()
    saved_source = document.model_dump_json()
    parsed = AnalysisRecord.model_validate_json(saved_answer)

    checked = validate_key_change_evidence(parsed, evidence(document), (document,))

    assert checked.status == "quote_present"
    assert checked.observed_text_span == (8, 8 + len(QUOTE))
    assert checked.quote_occurrences == 1
    assert checked.source_complete is True and checked.coverage == "complete"
    assert checked.semantic == "semantic_not_assessed"
    assert checked.block_origins == checked.paid_origins == "unmapped"
    assert parsed.model_dump_json() == saved_answer == answer.model_dump_json()
    assert document.model_dump_json() == saved_source
    assert "cited_claims" not in parsed.analysis.model_dump()


def test_missing_legacy_citations_are_visible_without_hiding_or_mutating_answer() -> None:
    answer = record()
    before = answer.model_dump_json()

    checked = validate_key_change_evidence(answer, None, ())

    assert checked == ClaimEvidenceCheck("legacy_missing")
    assert checked.source_complete is None and checked.coverage == "unknown"
    assert answer.model_dump_json() == before


@pytest.mark.parametrize("index", [1, 2, 100])
def test_claim_address_is_bound_to_index_and_exact_claim_text(index: int) -> None:
    document = source()
    checked = validate_key_change_evidence(
        record(), replace(evidence(document), index=index), (document,)
    )

    assert checked.status == ("claim_hash_mismatch" if index == 1 else "claim_index_invalid")


@pytest.mark.parametrize("changed", [CLAIM.replace("30", "60"), CLAIM + " ", "Не " + CLAIM])
def test_revised_claim_cannot_reuse_previous_binding(changed: str) -> None:
    answer = record()
    answer.analysis.key_changes[0] = changed
    document = source()

    checked = validate_key_change_evidence(answer, evidence(document), (document,))

    assert checked.status == "claim_hash_mismatch"


@pytest.mark.parametrize("missing", ["absent", "wrong_identity", "empty"])
def test_source_unavailability_is_explicit(missing: str) -> None:
    document = source()
    documents = (
        ()
        if missing == "absent"
        else (document.model_copy(update={"identity": "other"}),)
        if missing == "wrong_identity"
        else (document.model_copy(update={"text": "", "complete": False}),)
    )

    checked = validate_key_change_evidence(record(), evidence(document), documents)

    assert checked.status == "source_unavailable"
    assert checked.coverage == ("partial" if missing == "empty" else "unknown")


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_document_identity_is_ambiguous_even_for_identical_copies(
    conflicting: bool,
) -> None:
    document = source()
    other = source(QUOTE + " Inny tekst.") if conflicting else document

    checked = validate_key_change_evidence(record(), evidence(document), (document, other))

    assert checked.status == "source_ambiguous"
    assert checked.coverage == "unknown"


@pytest.mark.parametrize("role", ["position", "response", "final_act", "commission_report"])
def test_only_project_text_is_admitted_as_proposal_evidence(role: DocumentRole) -> None:
    document = source(role=role)

    checked = validate_key_change_evidence(record(), evidence(document), (document,))

    assert checked.status == "source_role_mismatch"
    assert checked.semantic == "semantic_not_assessed"


@pytest.mark.parametrize("binding", ["expected", "declared", "text", "file"])
def test_raw_source_hash_is_verified_independently_of_other_hashes(binding: str) -> None:
    document = source()
    claim = evidence(document)
    if binding == "expected":
        claim = replace(claim, source_text_sha256="e" * 64)
    elif binding == "declared":
        document = document.model_copy(update={"sha256": "e" * 64})
    elif binding == "text":
        document = document.model_copy(update={"text": QUOTE.replace("30", "60")})
    else:
        claim = replace(claim, source_text_sha256="f" * 64)

    checked = validate_key_change_evidence(record(), claim, (document,))

    assert checked.status == "source_hash_mismatch"


def test_versioned_normalized_identity_cannot_replace_raw_source_hash() -> None:
    document = source()

    with pytest.raises(ValueError, match="raw-text"):
        replace(evidence(document), source_text_sha256=document.normalized_sha256)


@pytest.mark.parametrize(
    "quote",
    [
        QUOTE.replace("30", "60"),
        QUOTE.replace("2027", "2028"),
        QUOTE.replace("studentów", "cudzoziemców"),
        QUOTE.replace("wynosi", "nie wynosi"),
        "",
    ],
)
def test_numbers_dates_scope_and_negation_cannot_change_inside_quote(quote: str) -> None:
    document = source()
    claim = replace(
        evidence(document),
        citation=EvidenceCitation(document_id=document.identity, locator="Art. 3", quote=quote),
    )

    checked = validate_key_change_evidence(record(), claim, (document,))

    assert checked.status == "citation_mismatch"


@pytest.mark.parametrize("locator", ["", "Art. 4", "sprawozdanie"])
def test_existing_locator_policy_is_required(locator: str) -> None:
    document = source()
    claim = replace(
        evidence(document),
        citation=EvidenceCitation(document_id=document.identity, locator=locator, quote=QUOTE),
    )

    assert validate_key_change_evidence(record(), claim, (document,)).status == "citation_mismatch"


@pytest.mark.parametrize("complete", [True, False])
def test_quote_presence_never_overrides_declared_partial_coverage(complete: bool) -> None:
    document = source().model_copy(update={"complete": complete})

    checked = validate_key_change_evidence(record(), evidence(document), (document,))

    assert checked.status == "quote_present"
    assert checked.source_complete is complete
    assert checked.coverage == ("complete" if complete else "partial")


def test_true_but_irrelevant_quote_remains_semantically_unassessed() -> None:
    unrelated = "Art. 3. Ustawa określa nazwę rejestru."
    document = source(unrelated)
    claim = replace(
        evidence(document),
        citation=EvidenceCitation(document_id=document.identity, locator="Art. 3", quote=unrelated),
    )

    checked = validate_key_change_evidence(record(), claim, (document,))

    assert checked.status == "quote_present"
    assert checked.semantic == "semantic_not_assessed"


@pytest.mark.parametrize("alternative", [QUOTE, QUOTE.replace(" ", "\n\t")])
def test_repetition_including_alternative_whitespace_has_no_unique_span(alternative: str) -> None:
    document = source(QUOTE + "\n" + alternative)

    checked = validate_key_change_evidence(record(), evidence(document), (document,))

    assert checked.status == "quote_ambiguous"
    assert checked.quote_occurrences == 2
    assert checked.observed_text_span is None


def test_overlapping_normalized_occurrences_are_ambiguous() -> None:
    document = source("Art. 3 Art. 3\nArt.\t3")
    claim = replace(
        evidence(document),
        citation=EvidenceCitation(
            document_id=document.identity, locator="Art. 3", quote="Art. 3 Art. 3"
        ),
    )

    checked = validate_key_change_evidence(record(), claim, (document,))

    assert checked.status == "quote_ambiguous"
    assert checked.quote_occurrences == 2
    assert checked.observed_text_span is None


def test_unique_normalized_quote_without_literal_identity_has_no_literal_span() -> None:
    document = source(QUOTE.replace(" ", "\n\t"))

    checked = validate_key_change_evidence(record(), evidence(document), (document,))

    assert checked.status == "quote_present"
    assert checked.quote_occurrences == 1
    assert checked.observed_text_span is None
    assert checked.block_origins == checked.paid_origins == "unmapped"


@pytest.mark.parametrize("bad_index", [-1, True])
def test_envelope_rejects_negative_or_boolean_indices(bad_index: int) -> None:
    with pytest.raises(ValueError, match="index"):
        replace(evidence(source()), index=bad_index)


@pytest.mark.parametrize("bad_hash", ["", "A" * 64, "0" * 63])
def test_envelope_rejects_malformed_hashes(bad_hash: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        replace(evidence(source()), claim_text_sha256=bad_hash)
    with pytest.raises(ValueError, match="SHA-256"):
        replace(evidence(source()), source_text_sha256=bad_hash)


def test_result_rejects_incoherent_quote_counts_and_spans() -> None:
    with pytest.raises(ValueError, match="one observed"):
        ClaimEvidenceCheck("quote_present")
    with pytest.raises(ValueError, match="multiple observed"):
        ClaimEvidenceCheck("quote_ambiguous", quote_occurrences=1)
    with pytest.raises(ValueError, match="cannot claim"):
        ClaimEvidenceCheck("legacy_missing", quote_occurrences=1)
    with pytest.raises(ValueError, match="unique literal"):
        ClaimEvidenceCheck("source_unavailable", observed_text_span=(0, 1))
    with pytest.raises(ValueError, match="declared source coverage"):
        ClaimEvidenceCheck("quote_present", quote_occurrences=1)


@pytest.mark.parametrize(
    ("locator", "quote", "admitted"),
    [
        ("Art. 3", QUOTE, True),
        ("art. 3.", QUOTE, True),
        ("§ 2", "§ 2. Termin wynosi 30 dni.", True),
        ("pkt 1", "pkt 1. Termin wynosi 30 dni.", True),
        ("lp. 1", "lp. 1\tTermin wynosi 30 dni.", True),
        ("1.", "1. Termin wynosi 30 dni.", False),
        ("1. Termin", "1. Termin wynosi 30 dni.", True),
        ("sprawozdanie", "sprawozdanie komisji", False),
        ("Art. 3", QUOTE.lower(), False),
        ("Art. 30", QUOTE, False),
        ("", QUOTE, False),
        ("Art. 3", "", False),
    ],
)
def test_extracted_matcher_preserves_existing_wrapper_contract(
    locator: str, quote: str, admitted: bool
) -> None:
    document = source(QUOTE if quote == QUOTE.lower() else "\n\t".join(quote.split()))
    citation = EvidenceCitation(document_id=document.identity, locator=locator, quote=quote)
    context = ReviewContext(
        number="1", title="Projekt", purpose="consultation_review", documents=(document,)
    )

    expected = document if admitted else None
    assert citation_document(citation, context, {"project_text"}) == expected
    assert citation_document_in(citation, (document,), {"project_text"}) == expected
    assert citation_document_in(citation, (document,), {"response"}) is None
    assert citation_document_in(citation, (), {"project_text"}) is None
    assert citation_document_in(None, (document,), {"project_text"}) is None


def test_existing_matcher_keeps_first_admissible_duplicate_behavior() -> None:
    original = source()
    other = source(QUOTE + " Inny tekst.")
    citation = evidence(original).citation
    context = ReviewContext(
        number="1", title="Projekt", purpose="consultation_review", documents=(other, original)
    )

    assert citation_document(citation, context, {"project_text"}) is other
    assert citation_document_in(citation, (other, original), {"project_text"}) is other


def test_group_evidence_binds_the_affected_group_slot() -> None:
    answer = record()
    group = "Студенты"
    answer.analysis.affected_groups = ["Другая группа", group]
    document = source().model_copy(update={"complete": False})
    group_evidence = AffectedGroupEvidence(
        index=1,
        claim_text_sha256=digest(group),
        source_text_sha256=document.sha256,
        citation=evidence(document).citation,
    )

    checked = validate_affected_group_evidence(answer, group_evidence, (document,))

    assert checked.status == "quote_present" and checked.observed_text_span == (0, len(QUOTE))
    assert checked.coverage == "partial" and checked.semantic == "semantic_not_assessed"
    assert checked.block_origins == checked.paid_origins == "unmapped"
    assert (
        validate_affected_group_evidence(
            answer, replace(group_evidence, index=0), (document,)
        ).status
        == "claim_hash_mismatch"
    )
    assert (
        validate_affected_group_evidence(
            answer, replace(group_evidence, index=2), (document,)
        ).status
        == "claim_index_invalid"
    )


def test_group_evidence_requires_a_project_text_quote() -> None:
    answer = record()
    answer.analysis.affected_groups = [CLAIM]
    document = source(role="position")
    key_evidence = evidence(document)
    group_evidence = AffectedGroupEvidence(
        index=key_evidence.index,
        claim_text_sha256=key_evidence.claim_text_sha256,
        source_text_sha256=key_evidence.source_text_sha256,
        citation=key_evidence.citation,
    )
    changed = group_evidence.citation.model_copy(
        update={"quote": QUOTE.replace("studentów", "osób")}
    )
    project_text = document.model_copy(update={"role": "project_text"})

    assert validate_affected_group_evidence(answer, None, ()) == ClaimEvidenceCheck(
        "legacy_missing"
    )
    assert (
        validate_affected_group_evidence(answer, group_evidence, (document,)).status
        == "source_role_mismatch"
    )
    assert (
        validate_affected_group_evidence(
            answer, replace(group_evidence, citation=changed), (project_text,)
        ).status
        == "citation_mismatch"
    )


def test_equal_claim_text_does_not_interchange_group_and_key_change_evidence() -> None:
    answer = record()
    answer.analysis.affected_groups = [CLAIM]
    document = source()
    key_evidence = evidence(document)

    with pytest.raises(TypeError, match="Expected affected-group evidence"):
        validate_affected_group_evidence(
            answer, cast(AffectedGroupEvidence, key_evidence), (document,)
        )


COMMENCEMENT_QUOTE = (
    "Art. 5. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia, "
    "z wyjątkiem art. 2, który wchodzi w życie z dniem 1 stycznia 2027 r."
)
COMMENCEMENT_CLAIM = "Через 14 дней после опубликования с исключением для статьи 2."


def commencement_evidence(document: ReviewDocument) -> ProposedCommencementEvidence:
    return ProposedCommencementEvidence(
        claim_text_sha256=digest(COMMENCEMENT_CLAIM),
        source_text_sha256=document.sha256,
        citation=EvidenceCitation(
            document_id=document.identity, locator="Art. 5", quote=COMMENCEMENT_QUOTE
        ),
    )


def test_proposed_commencement_binds_only_the_literal_quote_in_project_text() -> None:
    answer = record()
    answer.analysis.effective_date = COMMENCEMENT_CLAIM
    document = source(COMMENCEMENT_QUOTE).model_copy(update={"complete": False})

    checked = validate_proposed_commencement_evidence(
        answer, commencement_evidence(document), (document,)
    )

    assert checked.status == "quote_present"
    assert checked.observed_text_span == (0, len(COMMENCEMENT_QUOTE))
    assert checked.coverage == "partial" and checked.semantic == "semantic_not_assessed"
    assert checked.block_origins == checked.paid_origins == "unmapped"
    answer.analysis.effective_date += " "
    assert (
        validate_proposed_commencement_evidence(
            answer, commencement_evidence(document), (document,)
        ).status
        == "claim_hash_mismatch"
    )


@pytest.mark.parametrize("value", [None, " "])
def test_proposed_commencement_distinguishes_unavailable_claim_from_legacy_evidence(
    value: str | None,
) -> None:
    answer = record()
    answer.analysis.effective_date = value
    document = source(COMMENCEMENT_QUOTE)

    assert validate_proposed_commencement_evidence(answer, None, ()) == ClaimEvidenceCheck(
        "legacy_missing"
    )
    assert validate_proposed_commencement_evidence(
        answer, commencement_evidence(document), (document,)
    ) == ClaimEvidenceCheck("claim_unavailable")


@pytest.mark.parametrize("role", ["final_act", "response"])
def test_proposed_commencement_cannot_use_enacted_or_procedural_source(role: DocumentRole) -> None:
    answer = record()
    answer.analysis.effective_date = COMMENCEMENT_CLAIM
    document = source(COMMENCEMENT_QUOTE, role=role)

    assert (
        validate_proposed_commencement_evidence(
            answer, commencement_evidence(document), (document,)
        ).status
        == "source_role_mismatch"
    )


@pytest.mark.parametrize(
    "quote",
    [
        COMMENCEMENT_QUOTE.replace("14", "30"),
        COMMENCEMENT_QUOTE.replace(" z wyjątkiem", " bez wyjątku"),
        COMMENCEMENT_QUOTE.replace("wchodzi w życie", "wchodzi\nw życie", 1),
    ],
)
def test_proposed_commencement_rejects_changed_duration_condition_or_literal_whitespace(
    quote: str,
) -> None:
    answer = record()
    answer.analysis.effective_date = COMMENCEMENT_CLAIM
    document = source(COMMENCEMENT_QUOTE)
    original = commencement_evidence(document)
    changed = replace(original, citation=original.citation.model_copy(update={"quote": quote}))

    checked = validate_proposed_commencement_evidence(answer, changed, (document,))

    assert checked == ClaimEvidenceCheck("citation_mismatch", document.complete)


def test_proposed_commencement_does_not_accept_an_indexed_claim_envelope() -> None:
    answer = record()
    answer.analysis.effective_date = CLAIM
    document = source()

    with pytest.raises(TypeError, match="Expected proposed-commencement evidence"):
        validate_proposed_commencement_evidence(
            answer, cast(ProposedCommencementEvidence, evidence(document)), (document,)
        )


PAIR_CLAIM = "В новом проекте предложен более длинный срок для студентов."
LATER_QUOTE = QUOTE.replace("30", "60")


def proposed_change() -> tuple[
    AnalysisRecord, ProposedChangeEvidence, tuple[ReviewDocument, ReviewDocument]
]:
    answer = record()
    answer.analysis.changes_since_previous = ["Другой пункт", PAIR_CLAIM]
    before = source(QUOTE, role="comparison_text").model_copy(update={"complete": False})
    after = source(LATER_QUOTE).model_copy(
        update={"identity": "project/two", "file_sha256": "a" * 64}
    )
    pair = ProposedChangeEvidence(
        index=1,
        claim_text_sha256=digest(PAIR_CLAIM),
        before=ProposalEditionEvidence(
            source_text_sha256=before.sha256,
            artifact_sha256=before.file_sha256 or "",
            citation=EvidenceCitation(document_id=before.identity, locator="Art. 3", quote=QUOTE),
        ),
        after=ProposalEditionEvidence(
            source_text_sha256=after.sha256,
            artifact_sha256=after.file_sha256 or "",
            citation=EvidenceCitation(
                document_id=after.identity, locator="Art. 3", quote=LATER_QUOTE
            ),
        ),
    )
    return answer, pair, (before, after)


def test_proposed_change_observes_both_literal_quotes_without_assessing_change() -> None:
    answer, pair, documents = proposed_change()
    before = answer.model_dump_json()

    checked = validate_proposed_change_evidence(answer, pair, documents)

    assert checked.both_literal_quotes_observed
    assert checked.before.observed_text_span == (0, len(QUOTE))
    assert checked.after.observed_text_span == (0, len(LATER_QUOTE))
    assert checked.before.coverage == "partial" and checked.after.coverage == "complete"
    assert checked.before.semantic == checked.after.semantic == "semantic_not_assessed"
    assert checked.before.paid_origins == checked.after.paid_origins == "unmapped"
    assert answer.model_dump_json() == before


@pytest.mark.parametrize(
    ("fault", "status"),
    [
        ("no_envelope", "legacy_missing"),
        ("wrong_index", "claim_index_invalid"),
        ("empty", "claim_unavailable"),
        ("wrong_claim", "claim_hash_mismatch"),
    ],
)
def test_proposed_change_binds_one_saved_claim_for_both_sources(fault: str, status: str) -> None:
    answer, pair, documents = proposed_change()
    if fault == "wrong_index":
        pair = replace(pair, index=2)
    elif fault == "empty":
        answer.analysis.changes_since_previous[1] = " "
    elif fault == "wrong_claim":
        answer.analysis.changes_since_previous[1] += " "

    checked = validate_proposed_change_evidence(
        answer, None if fault == "no_envelope" else pair, documents
    )

    assert not checked.both_literal_quotes_observed
    assert checked.before.status == checked.after.status == status


@pytest.mark.parametrize("field", ["document_id", "artifact_sha256", "source_text_sha256"])
def test_proposed_change_rejects_relabeled_same_edition(field: str) -> None:
    _, pair, _ = proposed_change()
    after = pair.after
    if field == "document_id":
        after = replace(
            after,
            citation=after.citation.model_copy(
                update={"document_id": pair.before.citation.document_id}
            ),
        )
    elif field == "artifact_sha256":
        after = replace(after, artifact_sha256=pair.before.artifact_sha256)
    else:
        after = replace(after, source_text_sha256=pair.before.source_text_sha256)

    with pytest.raises(ValueError, match="distinct source editions"):
        replace(pair, after=after)


@pytest.mark.parametrize(
    ("fault", "status"),
    [
        ("final_act", "source_role_mismatch"),
        ("wrong_artifact", "source_hash_mismatch"),
        ("missing_artifact", "source_unavailable"),
        ("mutated_quote", "citation_mismatch"),
        ("normalized_only", "citation_mismatch"),
    ],
)
def test_proposed_change_preserves_failed_side_without_borrowing_other_quote(
    fault: str, status: str
) -> None:
    answer, pair, (before, after) = proposed_change()
    if fault == "final_act":
        after = after.model_copy(update={"role": "final_act"})
    elif fault in ("wrong_artifact", "missing_artifact"):
        after = after.model_copy(
            update={"file_sha256": "0" * 64 if fault == "wrong_artifact" else None}
        )
    else:
        changed = (
            LATER_QUOTE.replace("60", "90")
            if fault == "mutated_quote"
            else LATER_QUOTE.replace(" termin", "  termin")
        )
        pair = replace(
            pair,
            after=replace(
                pair.after, citation=pair.after.citation.model_copy(update={"quote": changed})
            ),
        )

    checked = validate_proposed_change_evidence(answer, pair, (before, after))

    assert not checked.both_literal_quotes_observed
    assert checked.before.status == "quote_present" and checked.after.status == status
    assert checked.after.semantic == "semantic_not_assessed"
