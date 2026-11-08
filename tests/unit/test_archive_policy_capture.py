from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from tools.replay_archive_dispositions import synthetic_inputs

from lexinform import archive_policy_capture
from lexinform.archive_policy_capture import (
    ArchivePolicyCapture,
    ArchivePolicyDecision,
    optional_archive_policy_capture,
)
from lexinform.document_candidates import select_document_parts
from lexinform.models import DocumentPart, ExtractedParts
from tests.unit.test_document_candidates import BILL
from tests.unit.test_physical_walk_capture import extractor


def test_policy_sink_preserves_original_slots_across_role_copies_and_sorted_versions() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(
            DocumentPart(text="", members=("z",)),
            DocumentPart(text=BILL, members=("z.docx",)),
            DocumentPart(text=BILL, members=("a.docx",)),
            DocumentPart(text="UZASADNIENIE\n" + "Treść " * 40, members=("uzasadnienie.docx",)),
        ),
    )
    ordinary = select_document_parts(physical, max_part_chars=400_000)
    capture = ArchivePolicyCapture()
    with capture.scope():
        capture.native(4)
        actual = select_document_parts(
            physical, max_part_chars=400_000, decision_sink=capture.record
        )
    assert actual == ordinary
    assert capture.decisions is not None, (
        "native slot accounting seals only fully corroborated policy decisions"
    )
    assert [(item.index, item.reason) for item in capture.decisions] == [
        (0, "empty_text"),
        (1, "version_preference"),
        (2, "selected_main"),
        (3, "selected_support"),
    ]


def test_no_main_family_does_not_invent_an_annex_family_exclusion() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(DocumentPart(text="Załącznik do ustawy\nTreść", members=("annex.docx",)),),
    )
    capture = ArchivePolicyCapture()
    capture.native(1)
    selected = select_document_parts(physical, max_part_chars=None, decision_sink=capture.record)
    assert selected.selection.state == "unsupported"
    assert capture.decisions is not None, (
        "native slot accounting seals only fully corroborated policy decisions"
    )
    assert capture.decisions[0].reason == "no_bill_candidate"


@pytest.mark.parametrize("fault", ["record", "scope", "native", "current", "constructor"])
def test_ordinary_capture_faults_preserve_native_selection(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("metadata only")

    data = synthetic_inputs()["synthetic_duplicate_names"]
    ordinary = extractor().extract_document_with_provenance(data).document
    if fault in ("record", "scope", "native"):
        monkeypatch.setattr(ArchivePolicyCapture, fault, fail)
    elif fault == "current":
        monkeypatch.setattr(archive_policy_capture, "current_archive_policy_capture", fail)
    else:
        monkeypatch.setattr(archive_policy_capture, "ArchivePolicyCapture", fail)
    with optional_archive_policy_capture():
        actual = extractor().extract_document_with_provenance(data).document
    assert actual == ordinary


@pytest.mark.parametrize("boundary", ["scope", "native", "record"])
def test_capture_cancellation_propagates_the_original_object(
    boundary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = KeyboardInterrupt("cancel")

    def cancel(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(ArchivePolicyCapture, boundary, cancel)
    with pytest.raises(KeyboardInterrupt) as caught, optional_archive_policy_capture():
        extractor().extract_document_with_provenance(
            synthetic_inputs()["synthetic_duplicate_names"]
        )
    assert caught.value is error, "the checked public fixture path guarantees this typed result"


def test_incomplete_or_oversized_policy_capture_cannot_certify_a_full_stream() -> None:
    capture = ArchivePolicyCapture()
    capture.native(1000)
    for index in range(1000):
        capture.record(
            ArchivePolicyDecision(index, "selected", "selected_main", "a" * 64, "b" * 64)
        )
    assert capture.decisions is None, (
        "native slot accounting seals only fully corroborated policy decisions"
    )


@pytest.mark.parametrize(
    "body_error", [None, ValueError("original"), KeyboardInterrupt("original")]
)
def test_scope_cleanup_metadata_fault_cannot_replace_the_original_body_result(
    body_error: BaseException | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def broken_scope(self: ArchivePolicyCapture) -> Iterator[None]:
        try:
            yield
        finally:
            raise RuntimeError("optional reset")

    monkeypatch.setattr(ArchivePolicyCapture, "scope", broken_scope)
    if body_error is None:
        with optional_archive_policy_capture():
            pass
    else:
        with pytest.raises(type(body_error)) as caught, optional_archive_policy_capture():
            raise body_error
        assert caught.value is body_error, (
            "the checked public fixture path guarantees this typed result"
        )


def test_policy_reasons_come_from_actual_main_family_and_exclusion_branches() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(
            DocumentPart(text=BILL, members=("pkg", "project.docx")),
            DocumentPart(text="Załącznik do ustawy\nTreść", members=("pkg", "annex.docx")),
            DocumentPart(text="Załącznik do ustawy\nTreść", members=("other", "annex.docx")),
            DocumentPart(text="UZASADNIENIE\nTreść", members=("pkg", "uzasadnienie.docx")),
            DocumentPart(text="UZASADNIENIE\nTreść", members=("pkg", "z_uzasadnienie.docx")),
            DocumentPart(text="", members=("empty.docx",)),
            DocumentPart(text="TABELA ZGODNOŚCI\nTreść", members=("table.docx",)),
            DocumentPart(text="Unrecognised", members=("misc.bin",)),
            DocumentPart(text="x" * 100, members=("projekt.docx",)),
            DocumentPart(text="UZASADNIENIE\nTreść", members=("pkg", "uzasadnienie_rozp.docx")),
            DocumentPart(text="fallback", members=("pkg", "projekt.docx")),
        ),
    )
    capture = ArchivePolicyCapture()
    capture.native(len(physical.parts))
    before = select_document_parts(physical, max_part_chars=50)
    actual = select_document_parts(physical, max_part_chars=50, decision_sink=capture.record)
    assert actual == before
    decisions = capture.decisions
    assert decisions is not None, "every actual branch has a compatible reader reason"
    assert [item.reason for item in decisions] == [
        "selected_main",
        "selected_annex",
        "family_exclusion",
        "selected_support",
        "rank_preference",
        "empty_text",
        "non_bill_body",
        "unknown_role",
        "long_unknown_text",
        "regulation_support",
        "rank_preference",
    ]


def test_version_conflict_prevents_package_composition_without_claiming_a_family() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(
            DocumentPart(text=BILL, members=("first.docx",)),
            DocumentPart(text=BILL.replace("200 zł", "300 zł"), members=("second.docx",)),
            DocumentPart(text="Załącznik do ustawy\nTreść", members=("annex.docx",)),
            DocumentPart(text="UZASADNIENIE\nTreść", members=("uzasadnienie.docx",)),
        ),
    )
    capture = ArchivePolicyCapture()
    capture.native(4)
    selected = select_document_parts(physical, max_part_chars=None, decision_sink=capture.record)
    assert selected.selection.state == "ambiguous"
    assert capture.decisions is not None, (
        "native slot accounting seals only fully corroborated policy decisions"
    )
    assert [item.reason for item in capture.decisions] == ["version_conflict"] * 4


@pytest.mark.parametrize(
    ("bills", "retained", "matching", "selected", "outcome"),
    [
        ((BILL, BILL, BILL), (1, 2, 0), (1, 2, 0), 1, "selected"),
        ((BILL, BILL, BILL.replace("200 zł", "400 zł")), (1, 2, 0), (), None, "text_conflict"),
        (
            (
                "Projekt z dnia 1 stycznia 2026\n" + BILL,
                "Projekt z dnia 2 stycznia 2026\n" + BILL,
                "Projekt z dnia 2 stycznia 2026\n" + BILL,
            ),
            (1, 2),
            (1, 2),
            1,
            "selected",
        ),
    ],
)
def test_archive_version_group_keeps_the_ordered_physical_slots(
    bills: tuple[str, ...],
    retained: tuple[int, ...],
    matching: tuple[int, ...],
    selected: int | None,
    outcome: str,
) -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=tuple(
            DocumentPart(text=text, members=(name,))
            for text, name in zip(bills, ("z.docx", "a.docx", "b.docx"), strict=True)
        ),
    )
    capture = ArchivePolicyCapture()
    capture.native(3)

    actual = select_document_parts(physical, max_part_chars=None, decision_sink=capture.record)

    assert actual == select_document_parts(physical, max_part_chars=None)
    assert capture.decisions is not None, "all three physical slots are accounted for"
    (group,) = tuple(row.version_group for row in capture.decisions if row.version_group)
    assert group.candidate_slots == (1, 2, 0)
    assert group.retained_slots == retained
    assert group.matching_slots == matching
    assert group.selected_slot == selected
    assert group.outcome == outcome
    assert (group.date_filter is not None) == (len(retained) < len(bills))


def test_archive_version_group_distinguishes_matching_bodies_from_conflicting_annexes() -> None:
    physical = ExtractedParts(
        input_sha256="a" * 64,
        parts=(
            *(
                DocumentPart(text=BILL, members=(root, "projekt.docx"))
                for root in ("a.zip", "b.zip", "c.zip")
            ),
            DocumentPart(text="Załącznik do ustawy\nTreść", members=("a.zip", "annex.docx")),
        ),
    )
    capture = ArchivePolicyCapture()
    capture.native(4)

    result = select_document_parts(physical, max_part_chars=None, decision_sink=capture.record)

    assert result.selection.state == "ambiguous"
    assert capture.decisions is not None, "all physical slots have observed policy reasons"
    (group,) = tuple(row.version_group for row in capture.decisions if row.version_group)
    assert group.candidate_slots == group.matching_slots == (0, 1, 2)
    assert group.selected_slot is None
    assert group.outcome == "annex_conflict"
