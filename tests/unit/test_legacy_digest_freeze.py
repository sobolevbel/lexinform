import json
from datetime import UTC, datetime

import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    DeliveryPlan,
    DigestPart,
    Publication,
    PublicationKind,
    PublicationStatus,
)
from lexinform.telegram_text import split_html

NOW = datetime(2026, 10, 28, tzinfo=UTC)
HTML = "<b>Срок 30 дней; 200 PLN; старые заявления.</b>\n"


def root(
    repo: SqliteBillRepository, *, text: str | None = None, patch: dict[str, object] | None = None
) -> Publication:
    identity = repo.create_publication(
        Publication(
            term=DIGEST_TERM,
            number=DIGEST_NUMBER,
            kind=PublicationKind.DIGEST,
            channel_id="readers",
            status=PublicationStatus.QUEUED,
            ref="2026-W43",
            created_at=NOW,
            delivery=None
            if text is None
            else DeliveryPlan(bill_json="", rendered_text=text).model_copy(update=patch or {}),
        )
    )
    value = repo.publication_by_id(identity)
    assert value is not None, "create_publication stores the digest identity"
    return value


def source(repo: SqliteBillRepository, *, channel: str = "readers") -> int:
    return repo.create_publication(
        Publication(
            term=10,
            number="3039",
            kind=PublicationKind.CONSULTATION_RESULTS,
            channel_id=channel,
            status=PublicationStatus.QUEUED,
            created_at=NOW,
            delivery=DeliveryPlan(bill_json="{}", destination="digest_only"),
        )
    )


def test_raw_opaque_plan_and_attempts_survive_the_narrow_future_freeze(
    repo: SqliteBillRepository,
) -> None:
    original = root(repo, text=HTML, patch={"render_origin": "legacy_snapshot"})
    assert original.id is not None and original.delivery is not None, (
        "root stores its semantic plan"
    )
    raw = original.delivery.model_dump_json()
    opaque = json.dumps(
        {**json.loads(raw), "future_opaque": {"keep": [False, 0, None, "payload"]}},
        separators=(",", ":"),
        ensure_ascii=False,
    )

    def quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    repo.restore(repo.dump().replace(quote(raw), quote(opaque)))
    repo.mark_publication(original.id, PublicationStatus.FAILED)
    current = repo.publication_by_id(original.id)
    assert current is not None, "the attempted physical root remains recorded"

    saved = repo.freeze_legacy_digest_parts(current, (DigestPart(text=HTML),), created_at=NOW)

    assert saved is not None and saved.delivery is not None, "the eligible future intent is stored"
    assert saved.id == current.id and saved.status == current.status and saved.attempts == 1
    assert saved.created_at == current.created_at
    assert (
        saved.delivery.policy_version == "legacy"
        and saved.delivery.render_origin == "legacy_snapshot"
    )
    assert '"future_opaque":{"keep":[false,0,null,"payload"]}' in repo.dump()
    before = repo.dump()
    repo.restore(before)
    assert repo.dump() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"bill_json": "{}"},
        {"policy_version": "future"},
        {"digest_parts": (99,)},
        {"digest_ref": "2026-W42"},
        {"reply_to": 111},
        {"edit_message_id": 111},
        {"approve_label": "Publish"},
        {"sitting_group": (99,)},
        {"continuation_root": 99},
        {"held_change_ids": (99,)},
        {"with_act": True},
    ],
)
def test_unsupported_context_is_not_adopted_or_rewritten(
    repo: SqliteBillRepository, patch: dict[str, object]
) -> None:
    original = root(repo, text=HTML, patch=patch)
    before = repo.dump()

    assert (
        repo.freeze_legacy_digest_parts(original, (DigestPart(text=HTML),), created_at=NOW) is None
    )
    assert repo.dump() == before


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.PENDING,
        PublicationStatus.UNKNOWN,
        PublicationStatus.SENT,
        PublicationStatus.DISMISSED,
        PublicationStatus.SKIPPED,
    ],
)
def test_uncertain_or_settled_root_never_acquires_new_future_parts(
    repo: SqliteBillRepository, status: PublicationStatus
) -> None:
    original = root(repo)
    assert original.id is not None, "root stores its physical identity"
    repo.mark_publication(original.id, status, count_attempt=False)
    current = repo.publication_by_id(original.id)
    assert current is not None, "mark_publication retains the row"
    before = repo.dump()

    assert (
        repo.freeze_legacy_digest_parts(current, (DigestPart(text=HTML),), created_at=NOW) is None
    )
    assert repo.dump() == before


@pytest.mark.parametrize(
    "problem",
    [
        "changed_body",
        "duplicated_source",
        "foreign_source",
        "completed_source",
        "attempted_source",
        "foreign_owner",
        "orphan_part",
        "changed_root",
    ],
)
def test_wrong_payload_or_ownership_cannot_partially_allocate_parts(
    repo: SqliteBillRepository, problem: str
) -> None:
    original = root(repo, text=HTML if problem == "changed_body" else None)
    identity = source(repo, channel="foreign" if problem == "foreign_source" else "readers")
    parts: tuple[DigestPart, ...] = (
        DigestPart(text="future introduction"),
        DigestPart(text=HTML, event_ids=(identity,)),
    )
    if problem == "duplicated_source":
        parts = (DigestPart(text=HTML, event_ids=(identity, identity)),)
    elif problem == "completed_source":
        repo.mark_publication(identity, PublicationStatus.SENT, message_id=777, sent_at=NOW)
    elif problem == "attempted_source":
        repo.mark_publication(identity, PublicationStatus.FAILED)
        repo.mark_publication(identity, PublicationStatus.QUEUED, count_attempt=False)
    elif problem == "foreign_owner":
        owner = repo.create_publication(
            Publication(
                term=DIGEST_TERM,
                number=DIGEST_NUMBER,
                kind=PublicationKind.DIGEST,
                channel_id="readers",
                status=PublicationStatus.QUEUED,
                ref="2026-W42",
                created_at=NOW,
                delivery=DeliveryPlan(
                    bill_json="", rendered_text=HTML, digest_event_ids=(identity,)
                ),
            )
        )
        repo.reserve_digest_events(owner, (identity,))
    elif problem == "orphan_part":
        repo.create_publication(
            Publication(
                term=DIGEST_TERM,
                number=DIGEST_NUMBER,
                kind=PublicationKind.DIGEST,
                channel_id="readers",
                status=PublicationStatus.UNKNOWN,
                ref="2026-W43:part:2",
                created_at=NOW,
            )
        )
    elif problem == "changed_root":
        repo.mark_publication(original.id or 0, PublicationStatus.FAILED)
    before = repo.dump()

    assert repo.freeze_legacy_digest_parts(original, parts, created_at=NOW) is None
    assert repo.dump() == before


def test_failed_root_cas_rolls_back_children_sequence_and_event_ownership(
    repo: SqliteBillRepository,
) -> None:
    original = root(repo)
    identity = source(repo)
    repo.restore(
        repo.dump()
        + "\nCREATE TRIGGER reject_freeze BEFORE UPDATE OF delivery_json ON publications BEGIN SELECT RAISE(IGNORE); END;\n"
    )
    before = repo.dump()

    with pytest.raises(ValueError, match="changed legacy digest root"):
        repo.freeze_legacy_digest_parts(
            original,
            (DigestPart(text="future introduction"), DigestPart(text=HTML, event_ids=(identity,))),
            created_at=NOW,
        )

    assert repo.dump() == before and repo.digest_owner(identity) is None


@pytest.mark.parametrize("reserved", [False, True])
def test_saved_html_can_only_move_its_exact_unattempted_owned_sources_to_the_final_part(
    repo: SqliteBillRepository,
    reserved: bool,
) -> None:
    identity = source(repo)
    text = HTML * 250
    original = root(repo, text=text, patch={"digest_event_ids": (identity,)})
    assert original.id is not None, "root stores the known whole-body owner"
    if reserved:
        repo.reserve_digest_events(original.id, (identity,))
    repo.mark_publication(original.id, PublicationStatus.FAILED)
    current = repo.publication_by_id(original.id)
    assert current is not None, "the attempted owner keeps its explicit membership"
    texts = split_html(text)
    parts = tuple(
        DigestPart(text=part, event_ids=(identity,) if index == len(texts) - 1 else ())
        for index, part in enumerate(texts)
    )

    saved = repo.freeze_legacy_digest_parts(current, parts, created_at=NOW)

    assert saved is not None and saved.delivery is not None, (
        "all future physical parts are recorded"
    )
    assert saved.attempts == 1 and saved.status is PublicationStatus.FAILED
    assert repo.digest_owner(identity) == saved.delivery.digest_parts[-1]
    assert repo.digest_sources(original.id) == []
    event = repo.publication_by_id(identity)
    assert event is not None and event.status is PublicationStatus.QUEUED


def test_missing_body_with_old_membership_is_not_rebuilt_as_different_coverage(
    repo: SqliteBillRepository,
) -> None:
    identity = source(repo)
    original = root(repo, text="", patch={"digest_event_ids": (identity,)})
    before = repo.dump()

    assert (
        repo.freeze_legacy_digest_parts(
            original, (DigestPart(text=HTML, event_ids=(identity,)),), created_at=NOW
        )
        is None
    )
    assert repo.dump() == before


def test_rebuilt_empty_body_does_not_inherit_a_legacy_snapshot_origin(
    repo: SqliteBillRepository,
) -> None:
    original = root(repo, text=" ", patch={"render_origin": "legacy_snapshot"})

    frozen = repo.freeze_legacy_digest_parts(original, (DigestPart(text=HTML),), created_at=NOW)

    assert frozen is not None and frozen.delivery is not None, (
        "the future body is a current rebuild"
    )
    assert frozen.delivery.render_origin is None


@pytest.mark.parametrize("ids", [(-1,), (0,), (999999,)])
def test_unknown_or_invalid_source_ids_do_not_reserve_future_coverage(
    repo: SqliteBillRepository, ids: tuple[int, ...]
) -> None:
    original = root(repo)
    before = repo.dump()

    assert (
        repo.freeze_legacy_digest_parts(
            original, (DigestPart(text=HTML, event_ids=ids),), created_at=NOW
        )
        is None
    )
    assert repo.dump() == before
