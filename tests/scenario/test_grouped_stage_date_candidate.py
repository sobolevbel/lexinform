from datetime import timedelta

import pytest

from lexinform.adapters.telegram import TelegramEditReceipt
from lexinform.models import ContentVersion, PublicationStatus, Stage
from lexinform.services.grouped_stage_date_candidate import (
    GroupedStageDateCorrection,
    build_grouped_stage_date_candidate,
)
from tests.checkpoint_state import saved_state
from tests.scenario.test_grouped_edit_dispatch import dispatch, reserved


@pytest.mark.parametrize("sibling_already_corrected", [False, True])
def test_only_exact_retained_referral_date_changes(
    sibling_already_corrected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    world, root, original, version, previous_render, slot = reserved(monkeypatch)
    if sibling_already_corrected:
        assert (
            dispatch(
                world,
                world.repo,
                lambda chat, message, _html: TelegramEditReceipt(chat, message),
                lambda: None,
                original,
                version,
                previous_render,
                slot,
            )
            is PublicationStatus.SENT
        )
    current: ContentVersion = version if sibling_already_corrected else original
    evidence = current.grouped_stage_evidence
    assert evidence is not None and evidence.render_witness is not None, "fixture freezes witness"
    assert (
        root.id is not None and current.rendered_text is not None and current.language is not None
    ), "the group has frozen HTML"
    baseline = evidence.render_witness.bind(current.rendered_text, current.language)
    selected = evidence.members[1]
    stage = Stage(
        stage_type="Referral",
        stage_name="Skierowanie",
        committee_code="ASW",
        date=baseline.today - timedelta(days=4),
    )
    assert stage.date is not None, "the exact fixture referral has a date"
    correction = GroupedStageDateCorrection(
        root_publication_id=root.id,
        member_publication_id=selected.publication_id,
        status_change_id=selected.status_change_id,
        expected_content_id=current.content_id,
        expected_sha256=baseline.sha256,
        expected_date=stage.date,
        corrected_date=stage.date + timedelta(days=2),
    )
    unchanged = world.repo.dump()
    with saved_state(unchanged) as restored:
        candidate = build_grouped_stage_date_candidate(restored.repo, world.formatter, correction)
        assert restored.repo.dump() == unchanged
    assert candidate is not None, "a retained referral proves the exact dated bullet"
    before = baseline.text.encode()
    after = candidate.text.encode()
    touched = []
    for old, new in zip(baseline.ranges, candidate.ranges, strict=True):
        assert (old.kind, old.owners) == (new.kind, new.owners)
        if before[old.start : old.end] != after[new.start : new.end]:
            touched.append((old.kind, old.owners))
            old_date = world.formatter.fmt_date(correction.expected_date).encode()
            new_date = world.formatter.fmt_date(correction.corrected_date).encode()
            assert (
                before[old.start : old.end].replace(
                    old_date + ": 📮".encode(), new_date + ": 📮".encode(), 1
                )
                == after[new.start : new.end]
            )
    assert touched == [("member", (baseline.members[1],))]
    for invalid in (
        correction.model_copy(update={"expected_sha256": "0" * 64}),
        correction.model_copy(update={"expected_content_id": "content-v1:" + "0" * 64}),
        correction.model_copy(update={"member_publication_id": evidence.members[0].publication_id}),
        correction.model_copy(update={"status_change_id": evidence.members[0].status_change_id}),
        correction.model_copy(update={"corrected_date": baseline.today + timedelta(days=1)}),
        correction.model_copy(update={"expected_date": baseline.today}),
        correction.model_copy(update={"corrected_date": stage.date - timedelta(days=1)}),
        correction.model_copy(update={"member_publication_id": "bad"}),
    ):
        assert build_grouped_stage_date_candidate(world.repo, world.formatter, invalid) is None
    assert world.repo.dump() == unchanged
