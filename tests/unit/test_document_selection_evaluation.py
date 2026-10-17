from tools.evaluate_document_selection import citation_fact_errors
from tools.probe_document_evidence import CandidateFact, Citation, EvidenceAnswer


def test_a_true_but_irrelevant_quote_does_not_prove_the_draft_date() -> None:
    text = "Projekt z dnia 1 maja 2026 r.\nUSTAWA\nArt. 1. W ustawie z dnia 1 maja 2020 r."
    answer = EvidenceAnswer(
        decision="prefer",
        preferred_ids=["A"],
        coverage="not_assessed",
        reason="",
        facts=[
            CandidateFact(
                candidate_id="A",
                role="bill",
                draft_date="2026-05-01",
                evidence=[Citation(block_id="A.head", quote="USTAWA")],
            )
        ],
    )
    question = {"candidates": [{"id": "A", "blocks": [{"id": "A.head", "text": text}]}]}

    assert "date citation lacks a heading date" in citation_fact_errors(answer, question)
    answer.facts[0].evidence.append(
        Citation(block_id="A.head", quote="Projekt z dnia 1 maja 2026 r.")
    )
    assert citation_fact_errors(answer, question) == []
    answer.facts[0].draft_date = "2020-05-01"
    answer.facts[0].evidence.append(
        Citation(block_id="A.head", quote="W ustawie z dnia 1 maja 2020 r.")
    )
    assert "date citation lacks a heading date" in citation_fact_errors(answer, question)
