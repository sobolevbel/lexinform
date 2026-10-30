import hashlib
from dataclasses import FrozenInstanceError

import pytest

from lexinform.models import DocumentFact, DocumentPart, DocumentSelection, TextDocument
from lexinform.source_graph_admission import source_graph_admission

SAFE = "https://source.test/draft.pdf"
UNSAFE = SAFE + "?token=PRIVATE"
SHA = "a" * 64


def document(url: str = SAFE, **updates: object) -> TextDocument:
    return TextDocument(url=url, kind="print").model_copy(update=updates)


@pytest.mark.parametrize(
    "field", ["url", "extra_urls", "normative_urls", "required_urls", "amendment_urls"]
)
@pytest.mark.parametrize("view", ["requested", "resolved"])
def test_every_document_url_field_is_checked_in_original_and_resolved_views(
    field: str, view: str
) -> None:
    changed = (
        document(UNSAFE) if field == "url" else document().model_copy(update={field: (UNSAFE,)})
    )
    requested, resolved = (changed, document()) if view == "requested" else (document(), changed)
    decision = source_graph_admission(
        requested, resolved, DocumentSelection(), opaque_failure_prose=False
    )
    assert not decision.admitted
    diagnostic = decision.omissions
    assert diagnostic is not None and diagnostic.total == 1
    assert (
        diagnostic.examples[0].requested_url_sha256 == hashlib.sha256(UNSAFE.encode()).hexdigest()
    )
    assert "PRIVATE" not in diagnostic.model_dump_json()


def test_original_nested_candidate_universe_is_checked_even_when_resolved_is_safe() -> None:
    requested = document(alternatives=(document(alternatives=(document(UNSAFE),)),))
    decision = source_graph_admission(
        requested, document(), DocumentSelection(), opaque_failure_prose=False
    )
    assert not decision.admitted and decision.omissions is not None
    assert decision.omissions.total == 1


@pytest.mark.parametrize("observed", [False, True])
def test_selected_and_observed_part_url_fields_are_checked(observed: bool) -> None:
    part = DocumentPart(url=UNSAFE)
    selection = DocumentSelection(
        parts=() if observed else (part,), observed_parts=(part,) if observed else ()
    )
    decision = source_graph_admission(document(), document(), selection, opaque_failure_prose=False)
    assert decision.omissions is not None and decision.omissions.total == 1


def test_evidence_url_occurrence_is_checked_without_borrowing_an_artifact_hash() -> None:
    fact = DocumentFact(
        kind="official_link",
        value="sejm/10/print/1",
        provenance="source_relation",
        part_index=0,
        source_url=UNSAFE,
        text_sha256=SHA,
        block_id="part:0",
        start=0,
        end=0,
        quote="",
    )
    selection = DocumentSelection(parts=(DocumentPart(url=UNSAFE, text_sha256=SHA),), facts=(fact,))
    decision = source_graph_admission(document(), document(), selection, opaque_failure_prose=False)
    assert decision.omissions is not None and decision.omissions.total == 2
    assert decision.omissions.examples[0].count == 2
    assert decision.omissions.examples[0].artifact_sha256 is None


def test_empty_physical_urls_remain_unknown_and_inputs_are_unchanged() -> None:
    requested = document(alternatives=(document("http://custom.test:12345/a.pdf"),))
    selection = DocumentSelection(
        parts=(DocumentPart(text="body", url=""),),
        observed_parts=(DocumentPart(url=""),),
        rejected=("opaque-looking text is not scanned or reinterpreted",),
    )
    before = requested.model_dump_json(), selection.model_dump_json()
    decision = source_graph_admission(requested, document(), selection, opaque_failure_prose=False)
    assert decision.admitted and decision.omissions is None
    assert (requested.model_dump_json(), selection.model_dump_json()) == before
    with pytest.raises(FrozenInstanceError):
        decision.__setattr__("omissions", None)


def test_empty_requested_url_is_invalid_instead_of_an_unknown_physical_origin() -> None:
    decision = source_graph_admission(
        document(""), document(), DocumentSelection(), opaque_failure_prose=False
    )
    assert decision.omissions is not None
    assert decision.omissions.reasons[0].reason == "invalid_url"


def test_explicit_opaque_prose_flag_omits_graph_without_retaining_or_scanning_prose() -> None:
    decision = source_graph_admission(
        document(),
        document(),
        DocumentSelection(rejected=("PRIVATE exception details",)),
        opaque_failure_prose=True,
    )
    assert decision.omissions is not None and decision.omissions.total == 1
    assert decision.omissions.examples[0].reason == "opaque_failure_prose"
    assert decision.omissions.examples[0].requested_url_sha256 is None
    assert "PRIVATE" not in decision.omissions.model_dump_json()


def test_unencodable_unicode_is_explicit_without_a_fake_hash_or_identity_creation() -> None:
    decision = source_graph_admission(
        document("https://source.test/\ud800.pdf"),
        document(),
        DocumentSelection(),
        opaque_failure_prose=False,
    )
    assert decision.omissions is not None
    assert decision.omissions.examples[0].reason == "invalid_unicode"
    assert decision.omissions.examples[0].requested_url_sha256 is None


def test_many_unsafe_fields_have_bounded_order_independent_hash_only_diagnostics() -> None:
    urls = tuple(f"{SAFE}?token=PRIVATE-{index}" for index in range(1000))
    first = source_graph_admission(
        document(extra_urls=urls), document(), DocumentSelection(), opaque_failure_prose=True
    )
    second = source_graph_admission(
        document(extra_urls=tuple(reversed(urls))),
        document(),
        DocumentSelection(),
        opaque_failure_prose=True,
    )
    assert first == second and first.omissions is not None
    assert first.omissions.total == 1001
    assert len(first.omissions.examples) <= 8
    assert len(first.omissions.model_dump_json().encode()) <= 2048
    assert "PRIVATE" not in first.omissions.model_dump_json()
    assert first.omissions.omitted_observations == 1001 - sum(
        example.count for example in first.omissions.examples
    )
