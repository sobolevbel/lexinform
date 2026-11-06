import hashlib

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.json_storage import canonical_json
from lexinform.models import AmendmentsContext, AmendmentsRecord, ComputationResult, ScannedDocument
from lexinform.models.amendment_inputs import AmendmentScanInputWitness
from lexinform.models.computation_inputs import ComputationInputBinding
from tests.computations import SHA, retained_result
from tests.unit.test_scan_input import URL, loaded_scan


def original() -> tuple[AmendmentScanInputWitness, AmendmentsContext, ScannedDocument]:
    _, scan = loaded_scan(cover_letter=False)
    assert scan.scan_input is not None, "the loader supplies the full PDF manifest"
    context = AmendmentsContext(
        number="3039",
        title="Frozen title",
        source_kind="committee_amendments",
        text="",
        truncated=False,
        previous_summary="Prior comparison",
        previous_key_changes=["Original prior change"],
        proposal="Original proposal",
        recommendation="Original recommendation",
        scan=scan,
    )
    witness = AmendmentScanInputWitness(
        source_url=URL,
        request_sha256=SHA,
        question_sha256=hashlib.sha256(
            canonical_json({"kind": "amendments", "ctx": context.model_dump(mode="json")}).encode()
        ).hexdigest(),
        context_json=canonical_json(context.model_dump(mode="json", exclude={"scan"})),
        scan_input=scan.scan_input,
    )
    return witness, context, scan


def paid_result(witness: AmendmentScanInputWitness) -> ComputationResult:
    result = retained_result("amendments")
    record = result.record
    assert isinstance(record, AmendmentsRecord), "the fixture has a validated amendment answer"
    return ComputationResult.model_validate(
        {
            **result.model_dump(),
            "record": record.model_copy(
                update={
                    "source_url": witness.source_url,
                    "source_kind": "committee_amendments",
                    "scan_input": witness.scan_input,
                }
            ),
            "input_binding": ComputationInputBinding(
                term=10, number=result.number, reuse_key="key"
            ),
            "amendment_scan_input": witness,
        }
    )


def test_witness_is_body_free_and_reconstitutes_only_matching_full_scan(
    repo: SqliteBillRepository,
) -> None:
    witness, context, scan = original()
    result = paid_result(witness)
    assert result.result_id == retained_result("amendments").result_id
    assert scan.data not in result.model_dump_json()
    assert witness.context_without_scan.scan is None

    repo.save_computation_result(result)
    dump = repo.dump()
    repo.restore(dump)
    repo.restore(dump)

    loaded = repo.get_computation_result(result.result_id)
    assert loaded == result and repo.dump() == dump
    assert loaded is not None and loaded.amendment_scan_input is not None, (
        "the reader retains the exact optional witness"
    )
    assert loaded.amendment_scan_input.restore_context(scan) == context

    invalid = witness.model_copy(update={"source_url": URL + "?other"})
    forged_dump = dump.replace(witness.model_dump_json(), invalid.model_dump_json())
    assert forged_dump != dump
    with pytest.raises(ValueError):
        repo.restore(forged_dump)
    assert repo.dump() == dump


@pytest.mark.parametrize("changed", ["data", "window", "source"])
def test_reconstruction_requires_exact_pdf_and_full_original_window(changed: str) -> None:
    witness, _, scan = original()
    updates: dict[str, object] = (
        {"data": "JVBERg=="}
        if changed == "data"
        else {"cover_letter_pages": 1}
        if changed == "window"
        else {"sha256": "b" * 64}
    )
    with pytest.raises(ValueError):
        witness.restore_context(scan.model_copy(update=updates))


def test_reconstruction_requires_the_original_comparison_context() -> None:
    witness, context, scan = original()
    changed_context = context.model_copy(update={"previous_summary": "New comparison"})
    changed = witness.model_copy(
        update={
            "context_json": canonical_json(
                changed_context.model_dump(mode="json", exclude={"scan"})
            )
        }
    )
    with pytest.raises(ValueError, match="invalid_amendment_scan_input"):
        changed.restore_context(scan)


@pytest.mark.parametrize("field", ["body", "window", "limit"])
def test_witness_rejects_body_context_foreign_windows_and_excess_metadata(field: str) -> None:
    witness, context, scan = original()
    raw = witness.model_dump(mode="json")
    if field == "body":
        raw["context_json"] = canonical_json(
            {**context.model_dump(mode="json", exclude={"scan"}), "scan": {"data": "encoded"}}
        )
    elif field == "window":
        loader, _ = loaded_scan()
        prefix = loader.first_pages(scan, 2)
        assert prefix is not None, "the controlled scan has a short prefix"
        raw["scan_input"] = prefix.scan_input
    else:
        raw["context_json"] = canonical_json(
            context.model_copy(update={"previous_summary": "x" * 3_600}).model_dump(
                mode="json", exclude={"scan"}
            )
        )
    with pytest.raises(ValueError, match="invalid_amendment_scan_input"):
        AmendmentScanInputWitness.model_validate(raw)


@pytest.mark.parametrize("field", ["owner", "source", "request", "manifest"])
def test_paid_result_requires_exact_witness_binding(field: str) -> None:
    witness, _, _ = original()
    result = paid_result(witness)
    raw = result.model_dump(mode="json")
    record = raw["record"]
    assert isinstance(record, dict), "the fixture retains a paid answer"
    if field == "owner":
        raw["number"] = "other"
    elif field == "source":
        record["source_url"] = URL + "?other"
    elif field == "request":
        raw["request_sha256"] = "b" * 64
    else:
        record.pop("scan_input")
    with pytest.raises(ValueError, match="invalid_amendment_scan_input"):
        ComputationResult.model_validate(raw)


def test_legacy_scan_manifest_does_not_become_a_question_witness() -> None:
    witness, _, _ = original()
    current = paid_result(witness)
    old = current.model_dump(mode="json", exclude={"amendment_scan_input"})
    loaded = ComputationResult.model_validate(old)
    assert loaded.amendment_scan_input is None and loaded.result_id == current.result_id
    assert loaded.model_dump(mode="json") == old
