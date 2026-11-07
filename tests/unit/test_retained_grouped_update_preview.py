from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest

from lexinform.grouped_update_preview import (
    GroupedUpdateRender,
    UpdateFragment,
    grouped_update_render,
)
from lexinform.models import ContentVersion, Publication, PublicationKind, PublicationStatus
from lexinform.models.content import (
    ContentHistoryView,
    GroupedStageContentEvidence,
    GroupedStageMember,
)
from lexinform.models.grouped_update_witness import GroupedRenderWitness
from lexinform.ports import BillRepository
from lexinform.services import grouped_update_preview
from lexinform.services.grouped_update_preview import preview_retained_grouped_update

DAY = date(2026, 9, 7)
MEMBERS = ((10, "3039", 101, 1), (10, "3040", 102, 2))


def render(
    first: str = "30 dni", second: str = "60 dni", shared: str = "Dalszy etap"
) -> GroupedUpdateRender:
    result = grouped_update_render(
        [
            UpdateFragment("header", (), "📰"),
            UpdateFragment("member", (MEMBERS[0],), first),
            UpdateFragment("member", (MEMBERS[1],), second),
            UpdateFragment("road", MEMBERS, shared),
        ],
        MEMBERS,
        today=DAY,
        language="ru",
        attention=True,
    )
    assert result is not None, "the ordinary render fixture has two members and one road"
    return result


def grouped_version() -> ContentVersion:
    original = render()
    witness = GroupedRenderWitness.from_render(original)
    assert witness is not None, "the synthetic fixture supplies exact event and card IDs"
    first, second = (
        GroupedStageMember(
            publication_id=3 + index,
            anchor_publication_id=1 + index,
            anchor_message_id=101 + index,
            status_change_id=1 + index,
            bill_snapshot_id="content-snapshot-v1:" + str(1 + index) * 64,
            change_snapshot_id="content-snapshot-v1:" + str(3 + index) * 64,
            original_bill_sha256="5" * 64,
        )
        for index in range(2)
    )
    return ContentVersion(
        root_publication_id=3,
        reason="original",
        language="ru",
        bill_snapshot_id=first.bill_snapshot_id,
        change_snapshot_id=first.change_snapshot_id,
        plan_snapshot_id="content-snapshot-v1:" + "6" * 64,
        rendered_text=original.text,
        formatter_version="test",
        grouped_stage_evidence=GroupedStageContentEvidence(
            members=(first, second), render_witness=witness
        ),
        status_change_id=1,
        recorded_at=datetime(2026, 9, 7, tzinfo=UTC),
    )


@pytest.fixture
def retained(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Mock, Mock, Publication, ContentVersion]:
    version = grouped_version()
    root = Publication(
        id=3,
        term=10,
        number="3039",
        kind=PublicationKind.STATUS_UPDATE,
        status=PublicationStatus.SENT,
        channel_id="@test",
        message_id=103,
        created_at=version.recorded_at,
        sent_at=version.recorded_at,
    )
    repo = Mock(spec=BillRepository)
    repo.publication_by_id.return_value = root
    repo.get_content_version.return_value = version
    inspection = Mock(
        return_value=ContentHistoryView(root_publication_id=3, current_id=version.content_id)
    )
    monkeypatch.setattr(grouped_update_preview, "inspect_content", inspection)
    return repo, inspection, root, version


def test_retained_preview_reads_exact_current_version_without_repository_writes(
    retained: tuple[Mock, Mock, Publication, ContentVersion],
) -> None:
    repo, inspection, root, version = retained
    candidate = render(first="90 dni")

    preview = preview_retained_grouped_update(
        repo,
        3,
        candidate,
        owner=MEMBERS[0],
        expected_content_id=version.content_id,
        expected_sha256=render().sha256,
    )

    assert preview is not None, "the confirmed baseline and target change are exact"
    assert preview.html == candidate.text
    assert preview.baseline_content_id == version.content_id
    assert preview.baseline_sha256 == render().sha256
    assert preview.candidate_sha256 == candidate.sha256
    inspection.assert_called_once_with(repo, root)
    assert [call[0] for call in repo.mock_calls] == ["publication_by_id", "get_content_version"]


@pytest.mark.parametrize(
    "reason",
    [
        "unknown",
        "unavailable",
        "stale_content",
        "wrong_root",
        "stale_sha",
        "missing_witness",
        "malformed_witness",
        "owner",
        "sibling",
        "shared",
        "regrouped",
        "oversize",
    ],
)
def test_retained_preview_refuses_unconfirmed_or_inexact_edits(
    reason: str,
    retained: tuple[Mock, Mock, Publication, ContentVersion],
) -> None:
    repo, inspection, root, version = retained
    candidate = render(first="90 dni")
    owner = MEMBERS[0]
    expected_id = version.content_id
    expected_sha = render().sha256
    evidence = version.grouped_stage_evidence
    assert evidence is not None and evidence.render_witness is not None, "the fixture is complete"
    if reason == "unknown":
        repo.publication_by_id.return_value = root.model_copy(
            update={"status": PublicationStatus.UNKNOWN, "message_id": None}
        )
    elif reason == "unavailable":
        inspection.return_value = ContentHistoryView(integrity="unavailable")
    elif reason == "stale_content":
        expected_id = "content-v1:" + "0" * 64
    elif reason == "wrong_root":
        inspection.return_value = ContentHistoryView(
            root_publication_id=4, current_id=version.content_id
        )
    elif reason == "stale_sha":
        expected_sha = "0" * 64
    elif reason in ("missing_witness", "malformed_witness"):
        witness = (
            None
            if reason == "missing_witness"
            else evidence.render_witness.model_copy(update={"text_sha256": "0" * 64})
        )
        version = version.model_copy(
            update={
                "grouped_stage_evidence": evidence.model_copy(update={"render_witness": witness})
            }
        )
        repo.get_content_version.return_value = version
        inspection.return_value = ContentHistoryView(
            root_publication_id=3, current_id=version.content_id
        )
        expected_id = version.content_id
    elif reason == "owner":
        owner = MEMBERS[1]
    elif reason == "sibling":
        candidate = render(first="90 dni", second="120 dni")
    elif reason == "shared":
        candidate = render(first="90 dni", shared="Inny etap")
    elif reason == "regrouped":
        candidate = replace(candidate, attention=False)
    else:
        candidate = replace(candidate, text=candidate.text + "x" * 4096)

    assert (
        preview_retained_grouped_update(
            repo,
            3,
            candidate,
            owner=owner,
            expected_content_id=expected_id,
            expected_sha256=expected_sha,
        )
        is None
    )
