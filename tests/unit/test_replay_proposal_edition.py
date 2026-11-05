from pathlib import Path
from typing import Any

import pytest
from tools.corpus_artifacts import digest
from tools.replay_proposal_edition import PinnedInput, official_print, prove_proposal

from lexinform.adapters.sejm_api import parse_print
from lexinform.models import DocumentPart, DocumentSelection, DocumentSelectionRecord
from lexinform.services.sources import original_document

BASE = "a" * 64
PROPOSAL = "b" * 64
PARENT = {"term": 10, "number": "4000", "title": "Projekt ustawy", "attachments": ["4000.pdf"]}
CHILD = {
    "term": 10,
    "number": "4000-A",
    "title": "Autopoprawka do projektu ustawy",
    "processPrint": ["4000"],
    "attachments": ["4000-A.pdf"],
}


def proof(
    *,
    proposal_update: dict[str, Any] | None = None,
    part_update: dict[str, Any] | None = None,
    document_update: dict[str, Any] | None = None,
) -> dict[str, object]:
    parent = parse_print({**PARENT, "additionalPrints": [CHILD]}, term=10)
    proposal = parse_print(CHILD, term=10).model_copy(update=proposal_update)
    document = original_document(parent)
    assert document is not None, "the official fixture has a named main PDF"
    record = DocumentSelectionRecord(
        selection=DocumentSelection(
            parts=(
                DocumentPart(role="bill", url=document.url, sha256=BASE, text_sha256=BASE),
                DocumentPart(
                    role="self_amendment",
                    url=document.amendment_urls[0],
                    sha256=PROPOSAL,
                    text_sha256=PROPOSAL,
                ).model_copy(update=part_update),
            ),
        )
    )
    return prove_proposal(
        parent=parent,
        proposal=proposal,
        document=document.model_copy(update=document_update),
        record=record,
        base_sha256=BASE,
        proposal_sha256=PROPOSAL,
    )


def test_proposal_proof_addresses_candidate_without_making_proposal_a_member() -> None:
    result = proof()

    assert result["relation"] == "proposes_change_to"
    assert result["parent_print"] == "4000"
    assert result["proposal_print"] == "4000-A"
    assert result["proposal_sha256"] == PROPOSAL
    assert result["selection_state"] == "unverified"
    assert result["edition_members"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"process_prints": ()},
        {"process_prints": ("4000", "4001")},
        {"process_prints": ("4001",)},
        {"term": 9},
        {"title": "Opinia do projektu"},
    ],
)
def test_title_and_filename_cannot_replace_exact_official_parent(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="same-term self-amendment"):
        proof(proposal_update=changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"role": "unknown"},
        {"sha256": "c" * 64},
        {"url": "https://example.invalid/4000-A.pdf"},
        {"members": ("nested/4000-A.pdf",), "container_sha256": PROPOSAL},
    ],
)
def test_proposal_requires_the_exact_selected_role_and_outer_pdf(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="distinct selected roles"):
        proof(part_update=changes)


def test_later_text_cannot_become_original_proposal_target() -> None:
    with pytest.raises(ValueError, match="original single-PDF"):
        proof(document_update={"kind": "text_after3"})


def test_official_listing_rejects_duplicate_or_cross_term_rows() -> None:
    with pytest.raises(ValueError, match="same-term"):
        official_print([PARENT, PARENT], term=10, number="4000")
    with pytest.raises(ValueError, match="same-term"):
        official_print([PARENT], term=9, number="4000")


def test_pins_reject_escape_and_changed_content(tmp_path: Path) -> None:
    data = b"official bytes"
    (tmp_path / "print.json").write_bytes(data)

    assert PinnedInput(path="print.json", sha256=digest(data)).read(tmp_path) == data
    with pytest.raises(ValueError, match="inside the corpus"):
        PinnedInput(path="../print.json", sha256=digest(data)).read(tmp_path)
    with pytest.raises(ValueError, match="SHA-256 differs"):
        PinnedInput(path="print.json", sha256=BASE).read(tmp_path)
