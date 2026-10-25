import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from tools import replay_cost_inputs
from tools.replay_cost_inputs import runtime_snapshot
from tools.replay_input_coverage import snapshot

from lexinform import sections
from lexinform.text_projection import TextProjection
from tests.scenario.test_document_selection_contract import law
from tests.unit.test_physical_extraction import docx


@pytest.mark.parametrize("batch", [False, True])
def test_runtime_replay_checks_actual_cost_cut_and_its_persisted_ranges(batch: bool) -> None:
    source = law() + "\n" + "Cudzoziemiec składa wniosek w terminie 30 dni.\n" * 6000

    result = runtime_snapshot(source, batch=batch, mapped=True)

    assert not result["core"]["errors"]
    assert result["mapping"]["round_trip"] and result["mapping"]["cuts"] == 1
    assert any(span["stage"] == "analysis" and span["end"] > 0 for span in result["coverage"])


def test_runtime_replay_rejects_a_trace_whose_source_identity_was_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = law() + "\n" + "Cudzoziemiec składa wniosek w terminie 30 dni.\n" * 6000
    original = sections.excerpt_projection

    def foreign_trace(
        text: str,
        spans: Sequence[tuple[int, int]],
        *,
        head_chars: int = 3000,
        window: int = 1200,
        max_chars: int = 24000,
        fill_head: bool = False,
    ) -> TextProjection:
        result = original(
            text,
            spans,
            head_chars=head_chars,
            window=window,
            max_chars=max_chars,
            fill_head=fill_head,
        )
        return TextProjection(result.text, "0" * 64, result.source_length, result.segments)

    monkeypatch.setattr(replay_cost_inputs, "excerpt_projection", foreign_trace)

    result = runtime_snapshot(source, batch=False, mapped=True)

    assert result["core"]["analysis_failures"] == 1
    assert result["core"]["record_sha256"] is None and result["mapping"] is None


def test_raw_replay_compares_real_runtime_inputs_under_the_read_only_guard(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = law() + "\n" + "Cudzoziemiec składa wniosek w terminie 30 dni.\n" * 6000
    data = docx(source)
    raw = corpus / "sample.docx"
    raw.write_bytes(data)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "suite": "input-coverage-v2",
                "status": "passed",
                "observations": {"sample.docx": snapshot(data, mapped=True)},
            }
        )
    )
    sha = hashlib.sha256(inventory.read_bytes()).hexdigest()
    before, after = tmp_path / "before.json", tmp_path / "after.json"

    assert replay_cost_inputs.run(corpus, inventory, sha, before, mapped=False) == 0
    assert (
        replay_cost_inputs.run(
            corpus,
            inventory,
            sha,
            after,
            mapped=True,
            reference=before,
            reference_sha256=hashlib.sha256(before.read_bytes()).hexdigest(),
        )
        == 0
    )

    result = json.loads(after.read_bytes())
    assert result["status"] == "passed" and not result["core_changes"] and not result["errors"]
    assert not result["changed_inputs"] and not result["changed_code"]
    cases = result["observations"]["sample.docx"]["cases"]
    assert len(cases) == 2 and all(case["mapping"]["round_trip"] for case in cases.values())

    raw.write_bytes(data + b"CHANGED")
    rejected = tmp_path / "rejected.json"
    assert replay_cost_inputs.run(corpus, inventory, sha, rejected, mapped=True) == 1
    assert "SHA mismatch" in json.loads(rejected.read_bytes())["errors"]["sample.docx"]
