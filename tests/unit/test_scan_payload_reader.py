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
from lexinform.models import (
    AmendmentsContext,
    AmendmentsQuestion,
    AnalysisQuestion,
    BatchIntent,
    BatchProvider,
    BatchRequest,
    BillContext,
    DigestItemMeta,
    SenateResolutionIdentity,
    SourceKind,
    SupplementContext,
    SupplementQuestion,
)
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


def committee_scan_request(factory: Callable[[], BatchBackend]) -> BatchRequest:
    primary = scan_request(offline_openai_backend, "analysis")
    question = primary.question
    assert isinstance(question, AnalysisQuestion), "the fixture supplies a primary scan"
    scan = question.ctx.scan
    assert scan is not None, "the fixture retains the manifested PDF"
    context = AmendmentsContext(
        number=primary.number,
        title="Committee amendment",
        source_kind="committee_amendments",
        text="",
        truncated=False,
        previous_summary="Original analysis",
        scan=scan,
    )
    return factory().prepare_request(
        BatchRequest(
            custom_id="committee-scan",
            term=10,
            number=primary.number,
            question=AmendmentsQuestion(ctx=context),
        )
    )


@pytest.mark.parametrize(
    ("provider", "factory"),
    [("openai", offline_openai_backend), ("anthropic", offline_anthropic_backend)],
)
def test_writer_compacts_committee_scan_and_reader_restores_exact_request(
    tmp_path: Path, provider: BatchProvider, factory: Callable[[], BatchBackend]
) -> None:
    frozen = committee_scan_request(factory)
    reference = compact(frozen)
    encoded = serialize_intent_request(frozen)
    assert json.loads(encoded) == reference
    assert parse_batch_request(reference) == frozen

    path = tmp_path / "committee-intent.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        question = frozen.question
        assert isinstance(question, AmendmentsQuestion), "the fixture freezes a committee request"
        scan = question.ctx.scan
        assert scan is not None, "the fixture supplies the saved PDF"
        intent = BatchIntent.from_request(
            request=frozen,
            meta=DigestItemMeta(
                memo_key=frozen.custom_id,
                source_kind=question.ctx.source_kind,
                scan_input=scan.scan_input,
            ),
            provider=provider,
            created_at=NOW,
        )
        repo.save_batch_intent(intent)
        with closing(sqlite3.connect(path)) as connection, connection:
            (actual,) = connection.execute("SELECT request_json FROM llm_batch_intents").fetchone()
            assert actual == encoded
            assert len(actual) < len(frozen.model_dump_json())
        saved = repo.dump()
        repo.restore(saved)
        repo.restore(saved)
        assert repo.dump() == saved
        assert repo.list_queued_batch_intents() == [intent]
        repo.mark_batch_intents_submitting([frozen.custom_id])
        (submitting,) = repo.list_submitting_batch_intents()
        assert factory().prepare_request(submitting.request) == frozen


@pytest.mark.parametrize(
    "fault", ["kind", "source_kind", "primary_field", "text", "slot", "identity", "manifest"]
)
def test_committee_scan_reference_rejects_changed_context_and_attachment(fault: str) -> None:
    raw = compact(committee_scan_request(offline_openai_backend))
    context, ref = raw["question"]["ctx"], raw["payload_json"]
    if fault == "kind":
        raw["question"]["kind"] = "supplement"
    elif fault == "source_kind":
        context["source_kind"] = "senate_amendments"
    elif fault == "primary_field":
        context["text_source"] = "scan"
    elif fault == "text":
        context["text"] = "Mixed input"
    elif fault == "slot":
        ref["prefix"] = ref["prefix"].replace('"file_data"', '"alternate"')
    elif fault == "identity":
        raw["custom_id"] = "another-request"
    elif fault == "manifest":
        context["scan"]["scan_input"]["input_sha256"] = "0" * 64
    payload = ref["prefix"] + context["scan"]["data"] + ref["suffix"]
    raw["payload_sha256"] = hashlib.sha256(payload.encode()).hexdigest()

    with pytest.raises(ValueError, match="invalid_input_request_json"):
        parse_batch_request(raw)


@pytest.mark.parametrize("fault", ["source_kind", "text", "missing_manifest", "duplicate"])
def test_writer_keeps_unproved_committee_scan_inline(fault: str) -> None:
    frozen = committee_scan_request(offline_openai_backend)
    question, payload = frozen.question, frozen.payload_json
    assert isinstance(question, AmendmentsQuestion) and payload is not None, (
        "the fixture freezes a committee amendment"
    )
    context = question.ctx
    scan = context.scan
    assert scan is not None, "the fixture provides an exact scan"
    if fault == "source_kind":
        context = context.model_copy(update={"source_kind": "senate_amendments"})
    elif fault == "text":
        context = context.model_copy(update={"text": "Mixed input"})
    elif fault == "missing_manifest":
        context = context.model_copy(update={"scan": scan.model_copy(update={"scan_input": None})})
    elif fault == "duplicate":
        payload = payload[:-1] + f', "second_copy": "{scan.data}"' + "}"
    changed = frozen.model_copy(
        update={
            "question": question.model_copy(update={"ctx": context}),
            "payload_json": payload,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }
    )

    assert serialize_intent_request(changed) == changed.model_dump_json()


def senate_scan_request(factory: Callable[[], BatchBackend]) -> BatchRequest:
    frozen = committee_scan_request(offline_openai_backend)
    question = frozen.question
    assert isinstance(question, AmendmentsQuestion), "the fixture supplies an amendment question"
    scan = question.ctx.scan
    assert scan is not None, "the fixture has the original full PDF"
    source = SenateResolutionIdentity(
        sha256=scan.sha256,
        subject="o cudzoziemcach",
        adopted_on=NOW.date(),
        scan=True,
        expected_subjects=("o cudzoziemcach",),
        pages=scan.pages,
    )
    context = question.ctx.model_copy(
        update={"source_kind": "senate_amendments", "source_check": source}
    )
    return factory().prepare_request(
        BatchRequest(
            custom_id="senate-scan",
            term=10,
            number=frozen.number,
            question=AmendmentsQuestion(ctx=context),
        )
    )


@pytest.mark.parametrize(
    ("provider", "factory"),
    [("openai", offline_openai_backend), ("anthropic", offline_anthropic_backend)],
)
def test_writer_compacts_full_senate_scan_and_preserves_frozen_request(
    tmp_path: Path, provider: BatchProvider, factory: Callable[[], BatchBackend]
) -> None:
    request = senate_scan_request(factory)
    reference = compact(request)
    assert json.loads(serialize_intent_request(request)) == reference
    assert parse_batch_request(reference) == request
    question = request.question
    assert isinstance(question, AmendmentsQuestion), "the fixture keeps the Senate source"
    scan = question.ctx.scan
    assert scan is not None, "the frozen context retains the original PDF"
    intent = BatchIntent.from_request(
        request=request,
        meta=DigestItemMeta(
            memo_key="senate",
            source_kind="senate_amendments",
            amendment_source=question.ctx.source_check,
            scan_input=scan.scan_input,
        ),
        provider=provider,
        created_at=NOW,
    )
    path = tmp_path / "senate.db"
    with closing(SqliteBillRepository(path)) as repo:
        repo.migrate()
        repo.save_batch_intent(intent)
        compact_dump = repo.dump()
        with closing(sqlite3.connect(path)) as connection, connection:
            (actual,) = connection.execute("SELECT request_json FROM llm_batch_intents").fetchone()
            assert json.loads(actual) == reference
            connection.execute(
                "UPDATE llm_batch_intents SET request_json=?", (request.model_dump_json(),)
            )
        inline = repo.dump()
        repo.restore(inline)
        assert repo.dump() == inline
        for _ in range(2):
            repo.restore(compact_dump)
            assert repo.dump() == compact_dump
            assert repo.list_queued_batch_intents() == [intent]
        repo.mark_batch_intents_submitting([request.custom_id])
        (submitting,) = repo.list_submitting_batch_intents()
        assert factory().prepare_request(submitting.request) == request


@pytest.mark.parametrize(
    "fault",
    [
        "house",
        "source_hash",
        "source_pages",
        "source_kind",
        "subject",
        "cover",
        "window",
        "source_bytes",
        "transform",
        "slot",
        "payload_hash",
    ],
)
def test_senate_compact_reference_refuses_mismatched_source_and_attachment(fault: str) -> None:
    raw = compact(senate_scan_request(offline_openai_backend))
    context = raw["question"]["ctx"]
    scan = context["scan"]
    source = context["source_check"]
    manifest = scan["scan_input"]
    ref = raw["payload_json"]
    if fault == "house":
        context["source_kind"] = "committee_amendments"
    elif fault == "source_hash":
        source["sha256"] = "0" * 64
    elif fault == "source_pages":
        source["pages"] += 1
    elif fault == "source_kind":
        source["scan"] = False
    elif fault == "subject":
        source["subject"] = "different"
    elif fault == "cover":
        scan["cover_letter_pages"] = 1
    elif fault == "window":
        manifest["first_page"] = 2
    elif fault == "source_bytes":
        manifest["source_bytes"] += 1
    elif fault == "transform":
        manifest["transformations"] = ["triage"]
    elif fault == "slot":
        ref["prefix"] = ref["prefix"].replace('"file_data"', '"unrelated_data"')
        raw["payload_sha256"] = hashlib.sha256(
            (ref["prefix"] + scan["data"] + ref["suffix"]).encode()
        ).hexdigest()
    else:
        raw["payload_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="invalid_input_request_json"):
        parse_batch_request(raw)


@pytest.mark.parametrize("fault", ["source_hash", "source_pages", "truncated", "missing_manifest"])
def test_senate_writer_keeps_unsupported_source_checks_inline(fault: str) -> None:
    request = senate_scan_request(offline_openai_backend)
    question = request.question
    assert isinstance(question, AmendmentsQuestion), "the fixture retains the Senate question"
    scan, source = question.ctx.scan, question.ctx.source_check
    assert scan is not None and source is not None, "the fixture supplies complete scan identity"
    updates: dict[str, object]
    if fault == "source_hash":
        updates = {"source_check": source.model_copy(update={"sha256": "0" * 64})}
    elif fault == "source_pages":
        updates = {"source_check": source.model_copy(update={"pages": scan.pages + 1})}
    elif fault == "truncated":
        updates = {"truncated": True}
    else:
        updates = {"scan": scan.model_copy(update={"scan_input": None})}
    changed = question.model_copy(update={"ctx": question.ctx.model_copy(update=updates)})
    request = request.model_copy(update={"question": changed})

    assert serialize_intent_request(request) == request.model_dump_json()


def supplement_scan_request(factory: Callable[[], BatchBackend], kind: SourceKind) -> BatchRequest:
    frozen = scan_request(offline_openai_backend, "analysis")
    question = frozen.question
    assert isinstance(question, AnalysisQuestion), "the fixture supplies a full manifested PDF"
    return factory().prepare_request(
        BatchRequest(
            custom_id="supplement-scan",
            term=10,
            number=frozen.number,
            question=SupplementQuestion(
                ctx=SupplementContext(
                    number=frozen.number,
                    title="Projekt ustawy",
                    document_title="Dokument dodatkowy",
                    source_kind=kind,
                    text="",
                    truncated=False,
                    scan=question.ctx.scan,
                    previous_summary="Описание",
                )
            ),
        )
    )


@pytest.mark.parametrize("factory", [offline_openai_backend, offline_anthropic_backend])
@pytest.mark.parametrize("kind", ["government_position", "impact_assessment"])
def test_supplement_writer_recovers_exact_frozen_request(
    factory: Callable[[], BatchBackend], kind: SourceKind
) -> None:
    frozen = supplement_scan_request(factory, kind)
    encoded = serialize_intent_request(frozen)
    assert json.loads(encoded) == compact(frozen)
    recovered = parse_batch_request(encoded)
    assert recovered == frozen
    assert factory().prepare_request(recovered) == frozen


@pytest.mark.parametrize(
    ("owner", "key", "value"),
    [
        ("context", "source_kind", "print"),
        ("context", "number", "3038"),
        ("context", "document_title", ""),
        ("context", "text", "unexpected text"),
        ("context", "truncated", True),
        ("context", "source_check", {"scan": True}),
        ("scan", "cover_letter_pages", 1),
        ("manifest", "first_page", 2),
        ("manifest", "source_sha256", "0" * 64),
        ("manifest", "source_bytes", 1),
        ("manifest", "transformations", ["triage"]),
        ("request", "payload_sha256", "0" * 64),
    ],
)
def test_supplement_reference_refuses_inexact_context(owner: str, key: str, value: object) -> None:
    raw = compact(supplement_scan_request(offline_openai_backend, "government_position"))
    context = raw["question"]["ctx"]
    scan = context["scan"]
    owners = {"request": raw, "context": context, "scan": scan, "manifest": scan["scan_input"]}
    owners[owner][key] = value
    with pytest.raises(ValueError, match="invalid_input_request_json"):
        parse_batch_request(raw)


def test_supplement_reference_refuses_hash_valid_non_attachment_slot() -> None:
    raw = compact(supplement_scan_request(offline_openai_backend, "impact_assessment"))
    scan = raw["question"]["ctx"]["scan"]
    ref = raw["payload_json"]
    ref["prefix"] = ref["prefix"].replace('"file_data"', '"unrelated_data"')
    raw["payload_sha256"] = hashlib.sha256(
        (ref["prefix"] + scan["data"] + ref["suffix"]).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="invalid_input_request_json"):
        parse_batch_request(raw)


@pytest.mark.parametrize(
    ("owner", "key", "value"),
    [
        ("context", "source_kind", "print"),
        ("context", "number", "3038"),
        ("context", "document_title", ""),
        ("context", "truncated", True),
        ("scan", "scan_input", None),
    ],
)
def test_supplement_writer_keeps_unsupported_contexts_inline(
    owner: str, key: str, value: object
) -> None:
    frozen = supplement_scan_request(offline_openai_backend, "government_position")
    raw = frozen.model_dump(mode="json")
    context = raw["question"]["ctx"]
    owners = {"context": context, "scan": context["scan"]}
    owners[owner][key] = value
    request = BatchRequest.model_validate(raw)
    assert serialize_intent_request(request) == request.model_dump_json()


def test_supplement_writer_keeps_non_attachment_slot_inline() -> None:
    frozen = supplement_scan_request(offline_openai_backend, "impact_assessment")
    raw = frozen.model_dump(mode="json")
    payload = raw["payload_json"].replace('"file_data"', '"unrelated_data"')
    raw["payload_json"] = payload
    raw["payload_sha256"] = hashlib.sha256(payload.encode()).hexdigest()
    changed = BatchRequest.model_validate(raw)
    assert serialize_intent_request(changed) == changed.model_dump_json()


@pytest.mark.parametrize("purpose", ["supplement", "senate"])
def test_secondary_scan_manifest_is_bound_on_save_and_restore(
    repo: SqliteBillRepository, purpose: str
) -> None:
    frozen = (
        supplement_scan_request(offline_openai_backend, "government_position")
        if purpose == "supplement"
        else senate_scan_request(offline_openai_backend)
    )
    question = frozen.question
    assert isinstance(question, (SupplementQuestion, AmendmentsQuestion)), (
        "The fixtures produce secondary scan questions"
    )
    scan = question.ctx.scan
    assert scan is not None and scan.scan_input is not None, "The fixtures include a scan manifest"
    meta = DigestItemMeta(memo_key="scan", scan_input=scan.scan_input)
    intent = BatchIntent.from_request(request=frozen, meta=meta, provider="openai", created_at=NOW)
    repo.save_batch_intent(intent)
    saved = repo.dump()
    repo.restore(saved)
    assert repo.dump() == saved
    assert repo.list_queued_batch_intents() == [intent]

    other = scan.scan_input.model_copy(update={"input_sha256": "f" * 64})
    invalid_meta = intent.meta.model_copy(update={"scan_input": other})
    invalid = intent.model_copy(update={"meta": invalid_meta})
    with pytest.raises(ValueError, match="Secondary scan metadata differs"):
        repo.save_batch_intent(invalid)
    forged = saved.replace(
        intent.meta.model_dump_json().replace("'", "''"),
        invalid_meta.model_dump_json().replace("'", "''"),
    )
    assert forged != saved
    with pytest.raises(ValueError):
        repo.restore(forged)
    assert repo.dump() == saved

    legacy = BatchIntent.from_request(
        request=frozen, meta=DigestItemMeta(memo_key="scan"), provider="openai", created_at=NOW
    )
    assert isinstance(legacy.meta, DigestItemMeta) and legacy.meta.scan_input is None
