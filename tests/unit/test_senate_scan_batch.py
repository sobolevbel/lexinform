import datetime as dt
import json
from collections.abc import Callable

import pytest
from anthropic import Anthropic
from openai import OpenAI

from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.models import (
    AmendmentsContext,
    BatchRequest,
    ScannedDocument,
    SenateResolutionIdentity,
)
from lexinform.models.batch import AmendmentsQuestion
from lexinform.ports import BatchBackend


def openai_backend() -> BatchBackend:
    return OpenAiAnalyzer(lambda: OpenAI(api_key="test"))


def anthropic_backend() -> BatchBackend:
    return AnthropicAnalyzer(Anthropic(api_key="test"), model="claude-opus-5")


@pytest.mark.parametrize("factory", [openai_backend, anthropic_backend])
def test_both_batch_providers_attach_all_scan_pages_and_require_source_evidence(
    factory: Callable[[], BatchBackend],
) -> None:
    backend = factory()
    source = SenateResolutionIdentity(
        sha256="raw",
        subject="o cudzoziemcach",
        adopted_on=dt.date(2026, 10, 13),
        scan=True,
        expected_subjects=("o cudzoziemcach",),
        pages=9,
    )
    ctx = AmendmentsContext(
        number="1929",
        title="Projekt",
        source_kind="senate_amendments",
        text="",
        truncated=False,
        previous_summary="Opis",
        source_check=source,
        scan=ScannedDocument(data="JVBERi0=", pages=9, of_pages=9, sha256="raw"),
    )
    request = BatchRequest(
        custom_id="test", term=10, number="1929", question=AmendmentsQuestion(ctx=ctx)
    )

    prepared = backend.prepare_request(request)
    text_request = backend.prepare_request(
        request.model_copy(
            update={
                "question": AmendmentsQuestion(
                    ctx=ctx.model_copy(update={"scan": None, "source_check": None})
                )
            }
        )
    )

    assert prepared.payload_json
    payload = json.loads(prepared.payload_json)
    body = payload.get("body", payload.get("params"))
    blocks = body["input"][1]["content"] if "input" in body else body["messages"][0]["content"]
    assert "JVBERi0=" in json.dumps(blocks[0])
    assert "source_evidence" in blocks[1]["text"] and "o cudzoziemcach" in blocks[1]["text"]
    assert "Przeczytaj wszystkie strony" in blocks[1]["text"]
    assert "amendment_pages" in prepared.payload_json
    assert prepared.estimated_cost_usd > text_request.estimated_cost_usd > 0
    assert backend.prepare_request(prepared) == prepared
