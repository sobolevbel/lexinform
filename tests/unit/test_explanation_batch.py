import json
from collections.abc import Callable

import pytest

from lexinform.adapters.llm_prompts import explanation_system_prompt
from lexinform.models import ApplicantType, BatchRequest, BillContext, CommandName, parse_command
from lexinform.models.batch import AnalysisQuestion
from lexinform.ports import BatchBackend
from tests.unit.test_senate_scan_batch import anthropic_backend, openai_backend


@pytest.mark.parametrize("factory", [openai_backend, anthropic_backend])
def test_explanation_batch_requires_substance_even_when_irrelevant(
    factory: Callable[[], BatchBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = factory()
    monkeypatch.setattr(backend, "count_input_tokens", lambda ctx: 100)
    request = BatchRequest(
        custom_id="operator-explanation",
        term=10,
        number="3039",
        question=AnalysisQuestion(
            kind="explanation",
            ctx=BillContext(
                number="3039",
                title="Projekt ustawy o danych",
                description="",
                document_date=None,
                applicant_type=ApplicantType.GOVERNMENT,
                text="Art. 1. Dostęp do danych urządzenia.",
                truncated=False,
                text_source="pdf",
            ),
        ),
    )

    prepared = backend.prepare_request(request)

    assert prepared.payload_json is not None
    payload = json.loads(prepared.payload_json)
    body = payload.get("body", payload.get("params"))
    system = body["input"][0]["content"] if "input" in body else body["system"][0]["text"]
    assert system == explanation_system_prompt("ru")
    assert "even when\nrelevant=false" in system
    assert "all people or entities affected" in system
    schema = (
        body["text"]["format"]["schema"]
        if "text" in body
        else body["output_config"]["format"]["schema"]
    )
    assert "summary" in schema["properties"] and "key_changes" in schema["properties"]


def test_explain_accepts_a_reference_and_rejects_publication_options() -> None:
    command = parse_command("/explain RPW/35146/2026")
    assert command is not None and command.name is CommandName.EXPLAIN
    assert command.error is None and command.ref is not None
    assert command.ref.value == "RPW/35146/2026"
    for option in ("force", "publish", "to=-1001234567"):
        invalid = parse_command(f"/explain RPW/35146/2026 {option}")
        assert invalid is not None and invalid.error is not None
