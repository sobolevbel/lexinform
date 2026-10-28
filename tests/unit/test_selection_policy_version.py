import pytest
from pydantic import ValidationError

from lexinform.document_selection import DOCUMENT_SELECTION_POLICY_VERSION
from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.scenario.test_document_selection_contract import law


def test_legacy_selection_keeps_unknown_policy_and_identical_json_shape() -> None:
    raw = DocumentSelection().model_dump(mode="json")
    assert "policy_version" not in raw
    restored = DocumentSelection.model_validate(raw)
    assert restored.policy_version is None and restored.model_dump(mode="json") == raw


def test_future_policy_identifier_survives_roundtrip_and_text_removal() -> None:
    selection = DocumentSelection(
        policy_version="future-policy-v42:opaque",
        parts=(DocumentPart(text="Art. 1. Cudzoziemiec składa wniosek."),),
        observed_parts=(DocumentPart(text="Załącznik"),),
    )
    restored = DocumentSelection.model_validate_json(selection.model_dump_json())
    stripped = restored.without_text()
    assert stripped.policy_version == "future-policy-v42:opaque"
    assert stripped.parts[0].text == stripped.observed_parts[0].text == ""
    assert restored.parts[0].text == selection.parts[0].text


@pytest.mark.parametrize("state", ["unverified", "waiting", "ambiguous", "unsupported", "selected"])
def test_policy_identifier_does_not_invent_selected_evidence(state: str) -> None:
    selection = DocumentSelection.model_validate(
        {"state": state, "policy_version": "future-policy-v42"}
    )
    assert selection.state == ("unverified" if state == "selected" else state)
    assert selection.policy_version == "future-policy-v42"


@pytest.mark.parametrize("version", ["", " ", "two versions", "x" * 129])
def test_policy_identifier_is_bounded_metadata(version: str) -> None:
    with pytest.raises(ValidationError):
        DocumentSelection(policy_version=version)


def test_new_source_decision_records_policy_without_relabeling_physical_cache() -> None:
    url = "https://api.test/main.pdf"
    gateway = FakeSejmGateway(files={url: b"source"})
    loader = TextLoader(
        {"api.test": gateway.download}, FakeTextExtractor(law(), page_count=0), max_bytes=1000
    )
    document = TextDocument(url=url, kind="print")

    selected, loaded = loader.select_document(document)
    repeated = loader.select_document(document)[1]

    assert selected == document and loaded.selection.state == "unverified"
    assert loaded.selection.policy_version == DOCUMENT_SELECTION_POLICY_VERSION
    assert repeated == loaded and loaded.text == law()
    assert loader.read(url).selection.policy_version is None
    assert sum(call.startswith("download:") for call in gateway.calls) == 1


def test_source_identity_conflict_keeps_its_rejection_and_records_evaluated_policy() -> None:
    gateway = FakeSejmGateway()
    loader = TextLoader({"api.test": gateway.download}, FakeTextExtractor(), max_bytes=1000)
    document = TextDocument(
        url="https://api.test/foreign.pdf", kind="rcl", official_link_conflict=True
    )

    _, loaded = loader.select_document(document)

    assert loaded.selection.state == "unsupported" and loaded.text is None
    assert loaded.selection.policy_version == DOCUMENT_SELECTION_POLICY_VERSION
    assert loaded.selection.rejected == (
        "https://api.test/foreign.pdf: official source identity conflict",
    )
    assert not gateway.calls


def test_version_conflict_records_policy_without_choosing_an_edition() -> None:
    first, second = "https://api.test/one.pdf", "https://api.test/two.pdf"
    gateway = FakeSejmGateway(files={first: b"one", second: b"two"})
    loader = TextLoader(
        {"api.test": gateway.download},
        FakeTextExtractor(page_count=0, by_content={b"one": law(), b"two": law(days=60)}),
        max_bytes=1000,
    )
    document = TextDocument(
        url=first,
        kind="rcl",
        compare_versions=True,
        alternatives=(TextDocument(url=second, kind="rcl"),),
    )

    _, loaded = loader.select_document(document)

    assert loaded.selection.state == "ambiguous" and loaded.text is None
    assert loaded.selection.policy_version == DOCUMENT_SELECTION_POLICY_VERSION
