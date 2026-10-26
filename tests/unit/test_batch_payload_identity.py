import hashlib
from collections.abc import Callable

import httpx2 as httpx
import pytest
from anthropic import Anthropic
from openai import OpenAI

from lexinform.adapters.llm_anthropic import AnthropicAnalyzer
from lexinform.adapters.llm_openai import OpenAiAnalyzer
from lexinform.models import AmendmentsContext, BatchRequest
from lexinform.models.batch import AmendmentsQuestion
from lexinform.ports import BatchBackend
from tests.unit.test_scan_input import loaded_scan
from tests.unit.test_senate_scan_batch import anthropic_backend, openai_backend


def request_for(*, scan: bool = False) -> BatchRequest:
    _, document = loaded_scan()
    return BatchRequest(
        custom_id="saved-input",
        term=10,
        number="3039",
        question=AmendmentsQuestion(
            ctx=AmendmentsContext(
                number="3039",
                title="Projekt ustawy o cudzoziemcach",
                source_kind="committee_amendments",
                text="" if scan else "W art. 1 kwotę 200 zł zastępuje się kwotą 300 zł.",
                truncated=False,
                previous_summary="Opis",
                scan=document if scan else None,
            )
        ),
    )


@pytest.mark.parametrize("factory", [openai_backend, anthropic_backend])
@pytest.mark.parametrize("scan", [False, True])
def test_prepared_request_binds_exact_utf8_payload_and_cannot_be_refrozen(
    factory: Callable[[], BatchBackend], scan: bool
) -> None:
    backend = factory()

    prepared = backend.prepare_request(request_for(scan=scan))
    restored = BatchRequest.model_validate_json(prepared.model_dump_json())

    assert prepared.payload_json is not None, "the provider prepared its frozen request"
    assert prepared.payload_sha256 == hashlib.sha256(prepared.payload_json.encode()).hexdigest()
    assert backend.prepare_request(restored) == prepared
    with pytest.raises(ValueError, match="already frozen"):
        restored.freeze(
            payload_json="{}", model="different", prompt_version="new", estimated_cost_usd=1
        )


@pytest.mark.parametrize("factory", [openai_backend, anthropic_backend])
def test_corrupted_payload_is_rejected_on_restore_and_before_provider_reuse(
    factory: Callable[[], BatchBackend],
) -> None:
    backend = factory()
    prepared = backend.prepare_request(request_for())
    assert prepared.payload_json is not None, "the provider prepared its frozen request"
    changed = prepared.payload_json + " "

    with pytest.raises(ValueError, match="payload SHA-256"):
        BatchRequest.model_validate({**prepared.model_dump(), "payload_json": changed})
    with pytest.raises(ValueError, match="payload SHA-256"):
        backend.prepare_request(prepared.model_copy(update={"payload_json": changed}))


@pytest.mark.parametrize("factory", [openai_backend, anthropic_backend])
def test_legacy_frozen_payload_is_reused_without_rebuilding_or_inventing_a_checksum(
    factory: Callable[[], BatchBackend],
) -> None:
    backend = factory()
    current = backend.prepare_request(request_for())
    old = current.model_dump(exclude={"payload_sha256"})
    restored = BatchRequest.model_validate(old)

    reused = backend.prepare_request(restored)

    assert reused.model_dump() == old
    assert reused.payload_sha256 is None and reused.payload_json == current.payload_json
    with pytest.raises(ValueError, match="already frozen"):
        reused.freeze(payload_json="{}", model="new", prompt_version="new", estimated_cost_usd=1)


def test_checksum_without_frozen_payload_cannot_certify_an_unprepared_request() -> None:
    raw = request_for().model_dump()

    with pytest.raises(ValueError, match="payload SHA-256"):
        BatchRequest.model_validate({**raw, "payload_sha256": "0" * 64})


@pytest.mark.parametrize("anthropic", [False, True])
def test_corrupted_payload_never_reaches_provider_upload_or_submission(anthropic: bool) -> None:
    calls: list[httpx.Request] = []

    def unexpected(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"error": "unexpected provider call"})

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        backend: BatchBackend = (
            AnthropicAnalyzer(Anthropic(api_key="test", http_client=client, max_retries=0))
            if anthropic
            else OpenAiAnalyzer(lambda: OpenAI(api_key="test", http_client=client, max_retries=0))
        )
        prepared = backend.prepare_request(request_for())
        corrupted = prepared.model_copy(update={"payload_json": "{}"})

        with pytest.raises(ValueError, match="payload SHA-256"):
            backend.submit([corrupted])

    assert not calls
