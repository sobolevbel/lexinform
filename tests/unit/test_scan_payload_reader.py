import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

import httpx2 as httpx
import pytest
from anthropic import Anthropic
from openai import OpenAI

from lexinform.adapters.input_receipts import parse_batch_request, serialize_intent_request
from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import AnalysisQuestion, BatchIntent, BatchProvider, BatchRequest, BillContext
from lexinform.ports import BatchBackend
from lexinform.services.documents import TextLoader
from tests.fakes import FakeSejmGateway, FakeTextExtractor
from tests.unit.test_input_receipts import NOW, meta, request
from tests.unit.test_scan_input import loaded_scan


def offline_anthropic_backend() -> BatchBackend:
    def tokens(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages/count_tokens"
        return httpx.Response(200, json={"input_tokens": 80_000})

    transport = httpx.MockTransport(tokens)
    return AnthropicAnalyzer(
        Anthropic(api_key="test", http_client=httpx.Client(transport=transport)),
        model="claude-opus-5",
    )


def offline_openai_backend() -> BatchBackend:
    def reject(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected OpenAI test request to {request.url.path}")

    transport = httpx.MockTransport(reject)
    return OpenAiAnalyzer(
        lambda: OpenAI(api_key="test", http_client=httpx.Client(transport=transport))
    )


def scan_request(
    factory: Callable[[], BatchBackend],
    purpose: Literal["analysis", "reanalysis"],
    *,
    small: bool = False,
) -> BatchRequest:
    if small:
        _, scan = loaded_scan()
    else:
        url = "https://api.test/large.pdf"
        gateway = FakeSejmGateway(files={url: b"%PDF-" + b"x" * 40_000})
        loader = TextLoader(
            {"api.test": gateway.download}, FakeTextExtractor("", page_count=4), max_bytes=50_000
        )
        loaded = loader.load_scan(url, cover_letter=False)
        assert loaded is not None, "the test PDF fits the size and page limits"
        scan = loaded
    ctx = request().ctx.model_copy(update={"text": "", "text_source": "scan", "scan": scan})
    assert isinstance(ctx, BillContext), "the test request supplies a BillContext"
    return factory().prepare_request(
        BatchRequest(
            custom_id="scan-input",
            term=10,
            number="3039",
            question=AnalysisQuestion(kind=purpose, ctx=ctx),
        )
    )


def compact(request: BatchRequest) -> dict[str, Any]:
    raw = request.model_dump(mode="json")
    data = raw["question"]["ctx"]["scan"]["data"]
    payload = raw["payload_json"]
    assert payload.count(data) == 1, "the real provider builder has a single attachment"
    prefix, suffix = payload.split(data)
    return {
        **raw,
        "payload_json": {"profile": "question-scan-base64-v1", "prefix": prefix, "suffix": suffix},
    }


@pytest.mark.parametrize(
    ("provider", "factory"),
    [("openai", offline_openai_backend), ("anthropic", offline_anthropic_backend)],
)
@pytest.mark.parametrize("purpose", ["analysis", "reanalysis"])
def test_native_writer_round_trips_exact_frozen_scan_request(
    tmp_path: Path,
    provider: BatchProvider,
    factory: Callable[[], BatchBackend],
    purpose: Literal["analysis", "reanalysis"],
) -> None:
    frozen = scan_request(factory, purpose)
    raw = compact(frozen)
    encoded = serialize_intent_request(frozen)
    assert json.loads(encoded) == raw
    assert parse_batch_request(raw) == frozen

    path = tmp_path / "scan-intent.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        intent = BatchIntent.from_request(
            request=frozen, meta=meta(), provider=provider, created_at=NOW
        )
        repo.save_batch_intent(intent)
        saved = repo.dump()
        with closing(sqlite3.connect(path)) as connection, connection:
            (actual,) = connection.execute("SELECT request_json FROM llm_batch_intents").fetchone()
            assert actual == encoded
            connection.execute(
                "UPDATE llm_batch_intents SET request_json=?", (frozen.model_dump_json(),)
            )
        inline = repo.dump()
        repo.restore(saved)
        repo.restore(saved)
        assert repo.dump() == saved
        assert repo.list_queued_batch_intents() == [intent]
        repo.mark_batch_intents_submitting([frozen.custom_id])
        (submitting,) = repo.list_submitting_batch_intents()
        assert factory().prepare_request(submitting.request) == frozen

        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE llm_batch_intents SET request_json=json_set("
                "request_json,'$.payload_sha256',?)",
                ("0" * 64,),
            )
        invalid = repo.dump()
        repo.restore(inline)
        with pytest.raises(ValueError, match="invalid_input_intent_snapshot"):
            repo.restore(invalid)
        assert repo.dump() == inline


@pytest.mark.parametrize(
    "fault",
    [
        "base64",
        "manifest",
        "manifest_hash",
        "media",
        "purpose",
        "missing_sha",
        "path",
        "preseeded_slot",
        "text",
        "extra_field",
        "pages",
    ],
)
def test_scan_reference_rejects_unproved_slots_and_native_context(fault: str) -> None:
    raw = compact(scan_request(offline_openai_backend, "analysis"))
    context, ref = raw["question"]["ctx"], raw["payload_json"]
    if fault == "base64":
        context["scan"]["data"] = "invalid@"
    elif fault == "manifest":
        context["scan"]["scan_input"] = None
    elif fault == "manifest_hash":
        context["scan"]["scan_input"]["input_sha256"] = "0" * 64
    elif fault == "media":
        context["scan"]["media_type"] = "text/plain"
    elif fault == "purpose":
        raw["question"]["kind"] = "explanation"
    elif fault == "missing_sha":
        raw.pop("payload_sha256")
    elif fault == "path":
        ref["prefix"] = ref["prefix"].replace('"file_data"', '"alternate"')
    elif fault == "preseeded_slot":
        ref["prefix"] += 'lexinform:scan-data-a", "extra_attachment": "'
    elif fault == "text":
        context["text"] = "duplicate"
    elif fault == "extra_field":
        ref["other"] = None
    elif fault == "pages":
        context["scan"]["pages"] = 601
    if fault != "missing_sha":
        payload = ref["prefix"] + context["scan"]["data"] + ref["suffix"]
        raw["payload_sha256"] = hashlib.sha256(payload.encode()).hexdigest()

    with pytest.raises(ValueError, match="invalid_input_request_json"):
        parse_batch_request(raw)


@pytest.mark.parametrize(
    "fault", ["small", "missing", "duplicate", "wrong_slot", "legacy", "mixed", "bad_manifest"]
)
def test_writer_keeps_unproved_scan_payload_inline(fault: str) -> None:
    frozen = scan_request(offline_openai_backend, "analysis", small=fault == "small")
    question, payload = frozen.question, frozen.payload_json
    assert isinstance(question, AnalysisQuestion) and payload is not None, "test freezes primary"
    context = question.ctx
    scan = context.scan
    assert scan is not None and scan.scan_input is not None, "test supplies a native scan"
    if fault == "missing":
        payload = payload.replace(scan.data, "inline")
    elif fault == "duplicate":
        payload = payload[:-1] + f', "second_copy": "{scan.data}"' + "}"
    elif fault == "wrong_slot":
        payload = payload.replace('"file_data"', '"other_field"')
    elif fault == "legacy":
        context = context.model_copy(update={"scan": scan.model_copy(update={"scan_input": None})})
    elif fault == "mixed":
        context = context.model_copy(update={"text": "Partial context"})
    elif fault == "bad_manifest":
        invalid = scan.scan_input.model_copy(update={"input_sha256": "0" * 64})
        context = context.model_copy(
            update={"scan": scan.model_copy(update={"scan_input": invalid})}
        )
    frozen = frozen.model_copy(
        update={
            "question": question.model_copy(update={"ctx": context}),
            "payload_json": payload,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }
    )

    assert serialize_intent_request(frozen) == frozen.model_dump_json()
