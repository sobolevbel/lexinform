import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from lexinform.document_editions import candidate_edition
from lexinform.document_selection import bill_selection
from lexinform.models import DocumentPart, DocumentSelection, DocumentSelectionRecord

NOW = datetime(2026, 10, 28, tzinfo=UTC)
URL = "https://official.test/project.pdf"
BILL = "Projekt z dnia 1 stycznia 2026 r.\nUSTAWA\no cudzoziemcach\nArt. 1. Cudzoziemiec składa wniosek.\nArt. 2. Ustawa wchodzi w życie po 30 dniach."


def source_record(*, extra: tuple[DocumentPart, ...] = ()) -> DocumentSelectionRecord:
    part = DocumentPart(
        url=URL,
        role="bill",
        sha256="a" * 64,
        text=BILL,
        text_sha256=hashlib.sha256(BILL.encode()).hexdigest(),
        text_end=len(BILL),
    )
    selected = bill_selection(
        DocumentSelection(parts=(part, *extra)), source_basis=("official/project/1",)
    )
    return DocumentSelectionRecord.from_selection(selected)


def test_candidate_membership_is_separate_from_question_quality_and_recording_time() -> None:
    first = source_record()
    assert first.selection.state == "selected", (
        "the fixture carries the four existing source proofs"
    )
    second = DocumentSelectionRecord.model_validate_json(
        first.model_copy(
            update={
                "selection": first.selection.model_copy(
                    update={
                        "question": "comparison",
                        "state": "unverified",
                        "policy_version": "other-policy",
                    }
                )
            }
        ).model_dump_json()
    )
    a, b = (
        candidate_edition(first, recorded_at=NOW),
        candidate_edition(second, recorded_at=NOW + timedelta(days=30)),
    )

    assert a is not None and b is not None, "both source choices have one known normative core"
    assert a.edition_id == b.edition_id and a.member_count == b.member_count == 1
    assert len(a.model_dump_json().encode()) < 1024
    assert BILL not in a.model_dump_json() and "facts" not in a.model_dump_json()


@pytest.mark.parametrize("role", ["justification", "osr", "self_amendment"])
def test_support_and_unconsolidated_proposals_do_not_enter_candidate_normative_membership(
    role: str,
) -> None:
    extra = DocumentPart.model_validate({"role": role, "sha256": "b" * 64, "text_sha256": "c" * 64})
    first, second = source_record(), source_record(extra=(extra,))
    a, b = candidate_edition(first, recorded_at=NOW), candidate_edition(second, recorded_at=NOW)

    assert a is not None and b is not None, "the same bill core stays known with optional support"
    assert a.edition_id == b.edition_id and b.member_count == 1


def test_aliases_and_container_provenance_do_not_assert_equivalence_of_different_bytes() -> None:
    original = source_record()
    part = original.selection.parts[0]
    alias = DocumentSelectionRecord.from_selection(
        DocumentSelection(
            parts=(
                part.model_copy(
                    update={
                        "url": "https://official.test/alias",
                        "members": ("nested.zip", "project.docx"),
                        "container_sha256": "b" * 64,
                    }
                ),
            )
        )
    )
    other = DocumentSelectionRecord.from_selection(
        original.selection.model_copy(
            update={"parts": (part.model_copy(update={"sha256": "d" * 64}),)}
        )
    )
    a, b, c = [candidate_edition(item, recorded_at=NOW) for item in (original, alias, other)]

    assert a is not None and b is not None and c is not None, (
        "all three sets have known component identities"
    )
    assert a.edition_id == b.edition_id
    assert a.edition_id != c.edition_id


@pytest.mark.parametrize(
    "problem",
    ["waiting", "ambiguous", "unsupported", "raw_unknown", "text_unknown", "two_bills", "no_bill"],
)
def test_incomplete_or_unresolved_membership_gets_no_manufactured_edition(problem: str) -> None:
    record = source_record()
    selected = record.selection
    if problem in ("waiting", "ambiguous", "unsupported"):
        selected = selected.model_copy(update={"state": problem})
    elif problem in ("raw_unknown", "text_unknown"):
        selected = selected.model_copy(
            update={
                "parts": (
                    selected.parts[0].model_copy(
                        update={"sha256" if problem == "raw_unknown" else "text_sha256": ""}
                    ),
                )
            }
        )
    elif problem == "two_bills":
        selected = selected.model_copy(update={"parts": (*selected.parts, selected.parts[0])})
    else:
        selected = selected.model_copy(update={"parts": ()})

    assert (
        candidate_edition(record.model_copy(update={"selection": selected}), recorded_at=NOW)
        is None
    )


def test_many_annexes_keep_complete_membership_without_expanding_the_manifest() -> None:
    annexes = tuple(
        DocumentPart(role="normative_annex", sha256=f"{index:064x}", text_sha256="b" * 64)
        for index in range(2000)
    )
    record = source_record().model_copy(
        update={
            "selection": DocumentSelection(
                state="unverified", parts=(source_record().selection.parts[0], *annexes)
            )
        }
    )

    edition = candidate_edition(record, recorded_at=NOW)

    assert edition is not None, "all normative components have exact recorded identities"
    assert edition.member_count == 2001
    assert len(edition.model_dump_json().encode()) < 1024


@pytest.mark.parametrize("change", ["order", "raw", "text", "text_range", "page_range"])
def test_normative_identity_preserves_order_and_exact_physical_coordinates(change: str) -> None:
    bill = source_record().selection.parts[0]
    annex = DocumentPart(role="normative_annex", sha256="b" * 64, text_sha256="c" * 64)
    parts = (bill, annex)
    if change == "order":
        altered = (annex, bill)
    else:
        fields: dict[str, dict[str, object]] = {
            "raw": {"sha256": "d" * 64},
            "text": {"text_sha256": "e" * 64},
            "text_range": {"text_start": 10, "text_end": 100},
            "page_range": {"page_start": 1, "page_end": 3},
        }
        altered = (bill, annex.model_copy(update=fields[change]))
    original = DocumentSelectionRecord.from_selection(DocumentSelection(parts=parts))
    changed = DocumentSelectionRecord.from_selection(DocumentSelection(parts=altered))

    first, second = (
        candidate_edition(original, recorded_at=NOW),
        candidate_edition(changed, recorded_at=NOW),
    )

    assert first is not None and second is not None, (
        "both recorded candidate cores have exact identities"
    )
    assert first.edition_id != second.edition_id
