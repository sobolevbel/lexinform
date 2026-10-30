from typing import Literal

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import AnalysisRecord, Editorial, Mnemonic, NameRecord, ResultApplication
from lexinform.name_binding import bind_mnemonic, mnemonic_matches_result
from lexinform.services.analysis_applications import apply_analysis
from lexinform.services.result_applications import record_retained_name_application
from tests.computations import NOW, retained_result
from tests.harness import summary


def test_paid_name_binding_is_prospective_and_preserves_unknown_legacy_json() -> None:
    result = retained_result("name")
    record = result.record
    assert isinstance(record, NameRecord), "the fixture creates a validated paid name"
    candidate = Mnemonic(
        name=record.proposal.name,
        language="ru",
        basis=record.proposal.basis,
        selected_at=record.created_at,
        origin="backfill",
    )
    before = result.model_dump_json()
    bound = bind_mnemonic(candidate, record, result)
    assert (
        bound.computation_id == result.result_id and bound.request_sha256 == result.request_sha256
    )
    assert mnemonic_matches_result(bound, result)
    assert result.model_dump_json() == before
    assert "computation_id" not in candidate.model_dump_json()
    assert "request_sha256" not in candidate.model_dump_json()
    assert Editorial(mnemonic=candidate).select(bound).mnemonic is candidate
    proposed = Editorial(mnemonic=candidate).select(
        candidate.model_copy(update={"name": "Inne imię"})
    )
    assert proposed.mnemonic is candidate and proposed.proposed is not None
    assert proposed.proposed.computation_id is None


@pytest.mark.parametrize(
    "change", ["missing", "request", "result", "name", "basis", "origin", "record_identity"]
)
def test_selected_name_binding_refuses_unknown_or_contradictory_sources(change: str) -> None:
    result = retained_result("name")
    record = result.record
    assert isinstance(record, NameRecord), "the fixture creates a validated paid name"
    candidate = Mnemonic(
        name=record.proposal.name,
        language="ru",
        basis=record.proposal.basis,
        selected_at=record.created_at,
        origin="backfill",
    )
    if change == "request":
        record = record.model_copy(update={"request_sha256": "f" * 64})
    elif change == "result":
        record = record.model_copy(update={"computation_id": "result-v1:" + "f" * 64})
    elif change == "name":
        candidate = candidate.model_copy(update={"name": "Another paid name"})
    elif change == "basis":
        candidate = candidate.model_copy(update={"basis": "Different factual basis"})
    elif change == "origin":
        candidate = candidate.model_copy(update={"origin": "operator"})
    elif change == "record_identity":
        result = result.model_copy(
            update={"record": record.model_copy(update={"computation_id": "result-v1:" + "f" * 64})}
        )
    assert bind_mnemonic(candidate, record, None if change == "missing" else result) == candidate
    assert candidate.computation_id is None


@pytest.mark.parametrize("kind", ["analysis", "reanalysis"])
def test_primary_name_binding_uses_the_actual_selected_paid_answer(
    kind: Literal["analysis", "reanalysis"],
) -> None:
    result = retained_result(kind)
    record = result.record
    assert isinstance(record, AnalysisRecord), "the fixture creates a primary paid result"
    answer = record.analysis.model_copy(
        update={"mnemonic_candidate": "Prawo pobytu", "mnemonic_basis": "The residence rule"}
    )
    record = record.model_copy(update={"analysis": answer})
    result = result.model_copy(update={"record": record})
    candidate = Mnemonic(
        name=answer.mnemonic_candidate,
        language=answer.mnemonic_language,
        basis=answer.mnemonic_basis,
        selected_at=record.created_at,
    )
    bound = bind_mnemonic(candidate, record, result)
    assert bound.computation_id == result.result_id
    assert not mnemonic_matches_result(bound.model_copy(update={"language": "pl"}), result)


def test_primary_writer_preserves_selected_binding_across_proposal_and_same_name(
    repo: SqliteBillRepository,
) -> None:
    result = retained_result("analysis")
    record = result.record
    assert isinstance(record, AnalysisRecord), "the fixture creates a primary paid result"
    answer = record.analysis.model_copy(
        update={"mnemonic_candidate": "Prawo pobytu", "mnemonic_basis": "The residence rule"}
    )
    record = record.model_copy(update={"analysis": answer})
    result = result.model_copy(update={"record": record})
    repo.save_computation_result(result)
    repo.upsert_summary(summary("3039", "Projekt ustawy"), now=NOW)
    apply_analysis(repo, 10, "3039", record, now=NOW)
    selected = repo.get(10, "3039")
    assert selected is not None and selected.editorial.mnemonic is not None, (
        "the actual primary writer selected its first valid name"
    )
    original = selected.editorial.mnemonic
    assert original.computation_id == result.result_id
    receipts = repo.list_result_applications(10, "3039")
    assert len([receipt for receipt in receipts if receipt.target is not None]) == 1
    for key, name in (("different", "Nowe prawo"), ("same", original.name)):
        later = result.model_copy(update={"custom_id": key})
        changed = record.model_copy(
            update={
                "analysis": answer.model_copy(update={"mnemonic_candidate": name}),
                "computation_id": later.result_id,
            }
        )
        later = later.model_copy(update={"record": changed})
        repo.save_computation_result(later)
        apply_analysis(repo, 10, "3039", changed, now=NOW)
        current = repo.get(10, "3039")
        assert current is not None, "the writer updates the existing owner"
        assert current.editorial.mnemonic == original
        if key == "different":
            assert current.editorial.proposed is not None, "a differing answer is only a proposal"
            assert current.editorial.proposed.computation_id is None
        assert len([row for row in repo.list_result_applications(10, "3039") if row.target]) == 1


def test_current_binding_without_source_projection_receipt_cannot_mint_transfer(
    repo: SqliteBillRepository,
) -> None:
    result = retained_result("name")
    record = result.record
    assert isinstance(record, NameRecord), "the fixture creates a validated paid name"
    repo.save_computation_result(result)
    repo.upsert_summary(summary("3039", "Projekt ustawy"), now=NOW)
    repo.upsert_summary(summary("3040", "Projekt ustawy"), now=NOW)
    name = bind_mnemonic(
        Mnemonic(
            name=record.proposal.name,
            language="ru",
            basis=record.proposal.basis,
            selected_at=NOW,
            origin="backfill",
        ),
        record,
        result,
    )
    repo.save_editorial(10, "3039", Editorial(mnemonic=name))
    before = repo.get(10, "3039")
    assert before is not None, "the fixture enrolled the original owner"
    with repo.atomic():
        repo.link_bills(10, "3039", "3040")
        target = repo.get(10, "3040")
        assert target is not None, "the fixture enrolled the print owner"
        record_retained_name_application(repo, before, target, now=NOW)
    assert not repo.list_result_applications(10, "3040")


def test_primary_binding_and_receipt_failure_roll_back_the_whole_projection(
    repo: SqliteBillRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = retained_result("analysis")
    record = result.record
    assert isinstance(record, AnalysisRecord), "the fixture creates a primary paid result"
    record = record.model_copy(
        update={
            "analysis": record.analysis.model_copy(
                update={"mnemonic_candidate": "Prawo pobytu", "mnemonic_basis": "Residence rule"}
            )
        }
    )
    result = result.model_copy(update={"record": record})
    repo.save_computation_result(result)
    repo.upsert_summary(summary("3039", "Projekt ustawy"), now=NOW)
    frozen = repo.dump()
    save = repo.save_result_application

    def reject(application: ResultApplication) -> None:
        save(application)
        if application.target is not None and application.target.kind == "editorial":
            raise RuntimeError("injected primary name receipt")

    monkeypatch.setattr(repo, "save_result_application", reject)
    with pytest.raises(RuntimeError, match="primary name receipt"):
        apply_analysis(repo, 10, "3039", record, now=NOW)
    assert repo.dump() == frozen
