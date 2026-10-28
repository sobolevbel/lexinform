import datetime as dt
import hashlib
from collections.abc import Callable
from typing import Literal

import pytest

from lexinform.models import RclLetterUse, RclLetterUseRecord
from lexinform.models.rcl_letter_use import (
    MAX_RCL_LETTER_OMISSION_BYTES,
    MAX_RCL_LETTER_USE_BYTES,
    RclLetterComponents,
    RclLetterDateArgument,
    RclLetterDescriptor,
    RclLetterInfo,
    RclLetterInput,
    RclLetterOfficial,
    RclLetterPhysical,
    RclLetterUrl,
    RclLetterUseOmission,
    RclLetterUsePayload,
    RclLetterWindow,
    RclLetterWindowFact,
)

NOW = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)
URL = "https://legislacja.rcl.gov.pl/docs/2/12402153/13156437/dokument737331.pdf"
TEXT = "Konsultacje publiczne: proszę o uwagi w terminie 30 dni. " * 10


def letter_payload(
    *, name: str = "Pismo.pdf", stage_id: int | None = 13156437
) -> RclLetterUsePayload:
    url = RclLetterUrl.of(URL)
    window = RclLetterWindow(
        letter_url=url,
        letter_date=dt.date(2026, 10, 1),
        days=30,
        deadline=dt.date(2026, 10, 31),
        email="uwagi@example.test",
    )
    result = RclLetterWindowFact.of(window)
    return RclLetterUsePayload(
        official=RclLetterOfficial(
            project_id=12402153,
            stage_id=stage_id,
            stage_number=3,
            letter_id=737331,
            letter=RclLetterDescriptor(name=name, url=url, created=dt.date(2026, 10, 2)),
        ),
        date_argument=RclLetterDateArgument(value=dt.date(2026, 10, 2), source="letter.created"),
        physical=RclLetterPhysical(state="unknown_cached_body"),
        input=RclLetterInput(
            sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
            chars=len(TEXT),
            utf8_bytes=len(TEXT.encode()),
        ),
        components=RclLetterComponents(
            letter_parser=2, parser_sha256="a" * 64, merge_sha256="b" * 64, python="3.14"
        ),
        letter_info=RclLetterInfo(
            letter_date=window.letter_date, days=window.days, email=window.email
        ),
        result=result,
        before=None,
        after=result,
        outcome="returned",
    )


def test_full_fact_roundtrip_unknown_origin_and_stage_identity() -> None:
    payload = letter_payload(stage_id=None)
    use = RclLetterUse.of(payload)
    record = RclLetterUseRecord(**use.model_dump(), recorded_at=NOW)

    assert RclLetterUseRecord.model_validate_json(record.model_dump_json()) == record
    assert record.payload.official.stage_id is None and record.payload.official.stage_number == 3
    assert record.payload.physical.artifact_sha256 is None
    assert len(record.model_dump_json().encode()) <= MAX_RCL_LETTER_USE_BYTES
    assert TEXT not in record.model_dump_json()
    assert "in_memory_only_not_saved_application" in record.model_dump_json()


def test_time_is_excluded_and_semantic_input_components_and_context_change_identity() -> None:
    payload = letter_payload()
    use = RclLetterUse.of(payload)
    first = RclLetterUseRecord(**use.model_dump(), recorded_at=NOW)
    later = RclLetterUseRecord(**use.model_dump(), recorded_at=NOW + dt.timedelta(days=365))
    assert first.use_id == later.use_id
    variants = [
        payload.model_copy(update={"input": payload.input.model_copy(update={"sha256": "c" * 64})})
        if payload.input
        else payload,
        payload.model_copy(
            update={"components": payload.components.model_copy(update={"parser_sha256": "c" * 64})}
        ),
        payload.model_copy(update={"before": payload.result}),
        payload.model_copy(
            update={"official": payload.official.model_copy(update={"stage_id": None})}
        ),
    ]
    assert all(RclLetterUse.of(value).use_id != use.use_id for value in variants)


@pytest.mark.parametrize("name", ["ą" * 257, "x" * 9000])
def test_overflow_retains_opaque_semantic_identity_without_truncated_descriptor(name: str) -> None:
    payload = letter_payload(name=name)
    use = RclLetterUse.of(payload)
    record = RclLetterUseRecord(**use.model_dump(), recorded_at=NOW)

    assert isinstance(use.payload, RclLetterUseOmission)
    assert use.payload_state == "omitted_over_limit"
    assert "descriptor_name_bytes" in use.payload.omitted
    assert use.payload.official.letter_url_sha256 == payload.official.letter.url.sha256
    assert use.payload.complete_metadata_revalidation == "unavailable"
    assert len(record.model_dump_json().encode()) <= MAX_RCL_LETTER_OMISSION_BYTES
    assert name not in record.model_dump_json()
    assert use.semantic_sha256 != RclLetterUse.of(letter_payload()).semantic_sha256
    assert RclLetterUseRecord.model_validate_json(record.model_dump_json()) == record


@pytest.mark.parametrize(
    "url",
    [
        "https://user:SECRET@legislacja.rcl.gov.pl/x.pdf",
        "https://legislacja.rcl.gov.pl/x.pdf?token=SECRET",
        "https://legislacja.rcl.gov.pl/x.pdf#SECRET",
        "https://legislacja.rcl.gov.pl:444/x.pdf",
        "https://legislacja.rcl.gov.pl/\nSECRET.pdf",
        "https://external.test/x.pdf",
    ],
)
def test_unsafe_url_has_hash_and_explicit_omission(url: str) -> None:
    value = RclLetterUrl.of(url)
    assert value.value is None and value.state == "omitted_unsafe"
    assert value.sha256 == hashlib.sha256(url.encode()).hexdigest()
    assert "SECRET" not in value.model_dump_json()
    with pytest.raises(ValueError, match="omitted URL"):
        RclLetterWindowFact.of(RclLetterWindow(letter_url=value))


@pytest.mark.parametrize("outcome", ["no_text", "read_error", "raised"])
def test_uncalled_parser_keeps_only_reached_result(
    outcome: Literal["no_text", "read_error", "raised"],
) -> None:
    payload = letter_payload()
    result = RclLetterWindowFact.of(RclLetterWindow(letter_url=payload.official.letter.url))
    unreadable = payload.model_copy(
        update={
            "input": None,
            "letter_info": None,
            "date_argument": RclLetterDateArgument(value=None, source="not_used"),
            "result": None if outcome == "raised" else result,
            "after": None if outcome == "raised" else result,
            "outcome": outcome,
            "error_class": None if outcome == "no_text" else "ValueError",
        }
    )
    use = RclLetterUse.of(unreadable)
    assert isinstance(use.payload, RclLetterUsePayload)
    assert use.payload.input is None and use.payload.letter_info is None
    assert (use.payload.after is None) == (outcome == "raised")
    invalid = unreadable.model_copy(update={"letter_info": payload.letter_info})
    with pytest.raises(ValueError):
        RclLetterUse.of(invalid)


@pytest.mark.parametrize("source", ["letter.created", "stage.modified", "project.modified"])
def test_date_fallback_is_an_exact_captured_argument(
    source: Literal["letter.created", "stage.modified", "project.modified"],
) -> None:
    payload = letter_payload()
    date = dt.date(2026, 10, 2)
    changed = payload.model_copy(
        update={"date_argument": RclLetterDateArgument(value=date, source=source)}
    )
    use = RclLetterUse.of(changed)
    assert isinstance(use.payload, RclLetterUsePayload)
    assert use.payload.date_argument.value == date and use.payload.date_argument.source == source
    with pytest.raises(ValueError):
        RclLetterDateArgument(value=None, source=source)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: {**value, "semantic_sha256": "c" * 64},
        lambda value: {**value, "payload_state": "omitted_over_limit"},
        lambda value: {**value, "recorded_at": "2026-10-28T00:00:00"},
    ],
)
def test_record_rejects_corrupt_identity_completeness_and_naive_time(
    mutate: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    use = RclLetterUse.of(letter_payload())
    record = RclLetterUseRecord(**use.model_dump(), recorded_at=NOW)
    with pytest.raises(ValueError):
        RclLetterUseRecord.model_validate(mutate(record.model_dump(mode="json")))


def test_window_output_and_unknown_origins_cannot_be_forged_by_model_copy() -> None:
    payload = letter_payload()
    assert payload.result is not None, "The fixture supplies a completed parse and merge"
    bad_result = payload.result.model_copy(update={"canonical_projection_sha256": "d" * 64})
    with pytest.raises(ValueError, match="projection hash"):
        RclLetterUse.of(payload.model_copy(update={"result": bad_result}))
    with pytest.raises(ValueError, match="unknown origin"):
        RclLetterPhysical(state="unknown_cached_body", artifact_sha256="a" * 64)
    with pytest.raises(ValueError, match="bounded identity"):
        RclLetterUse(
            use_id="rcl-letter-use-v1:" + "a" * 64,
            semantic_sha256="a" * 64,
            payload_state="complete",
            payload=letter_payload(name="x" * 9000),
        )


def test_total_record_budget_omits_without_individual_field_overflow() -> None:
    payload = letter_payload()
    url = RclLetterUrl.of("https://legislacja.rcl.gov.pl/" + "x" * 800 + ".pdf")
    assert payload.result is not None, "The fixture supplies a parsed result"
    result = RclLetterWindowFact.of(
        payload.result.projection.model_copy(update={"letter_url": url})
    )
    official = payload.official.model_copy(
        update={"letter": payload.official.letter.model_copy(update={"url": url})}
    )
    changed = payload.model_copy(
        update={"official": official, "result": result, "before": result, "after": result}
    )
    assert not changed.omission_reasons()
    use = RclLetterUse.of(changed)
    assert isinstance(use.payload, RclLetterUseOmission)
    assert use.payload.omitted == ("record_bytes",)
