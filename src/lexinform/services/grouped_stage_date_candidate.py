"""Build a dated-stage correction from verified retained group input."""

from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from lexinform.grouped_update_preview import (
    GroupedUpdateRender,
    UpdateOwner,
    preview_grouped_update,
    replace_grouped_update_member,
)
from lexinform.json_storage import read_json
from lexinform.models import PublicationStatus, Stage, StatusChange, told_stages
from lexinform.ports import BillRepository
from lexinform.services.content_history import FORMATTER_VERSION
from lexinform.services.content_inspection import inspect_content


class GroupedStageDateCorrection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    root_publication_id: int = Field(gt=0)
    member_publication_id: int = Field(gt=0)
    status_change_id: int = Field(gt=0)
    expected_content_id: str = Field(pattern=r"^content-v1:[0-9a-f]{64}$")
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_date: date
    corrected_date: date


class GroupedStageBulletRenderer(Protocol):
    @property
    def language(self) -> str | None: ...

    def grouped_stage_bullet(self, stage: Stage, *, today: date) -> str | None: ...

    def grouped_referral_date_bullet(self, stage: Stage, *, today: date) -> str | None: ...


def _member_entry(render: GroupedUpdateRender, owner: UpdateOwner) -> str | None:
    entries = [
        render.text.encode()[span.start : span.end].decode()
        for span in render.ranges
        if (span.kind, span.owners) == ("member", (owner,))
    ]
    return entries[0] if len(entries) == 1 else None


def build_grouped_stage_date_candidate(
    repo: BillRepository,
    formatter: GroupedStageBulletRenderer,
    correction: GroupedStageDateCorrection,
) -> GroupedUpdateRender | None:
    try:
        checked = GroupedStageDateCorrection.model_validate(correction.model_dump(warnings=False))
        return _build_candidate(repo, formatter, checked)
    except ValueError, TypeError:
        return None


def _build_candidate(
    repo: BillRepository,
    formatter: GroupedStageBulletRenderer,
    correction: GroupedStageDateCorrection,
) -> GroupedUpdateRender | None:
    root = repo.publication_by_id(correction.root_publication_id)
    if root is None or root.status is not PublicationStatus.SENT:
        return None
    history = inspect_content(repo, root)
    if (
        history is None
        or history.integrity != "valid"
        or history.ambiguous
        or history.current_id != correction.expected_content_id
    ):
        return None
    current = repo.get_content_version(correction.expected_content_id)
    if (
        current is None
        or current.root_publication_id != correction.root_publication_id
        or current.formatter_version != FORMATTER_VERSION
        or current.rendered_text is None
        or current.language is None
        or (evidence := current.grouped_stage_evidence) is None
        or (witness := evidence.render_witness) is None
    ):
        return None
    baseline = witness.bind(current.rendered_text, current.language)
    if baseline.sha256 != correction.expected_sha256 or formatter.language != baseline.language:
        return None
    for index, member in enumerate(evidence.members):
        if (member.publication_id, member.status_change_id) == (
            correction.member_publication_id,
            correction.status_change_id,
        ):
            return _replace_referral_date(
                repo,
                formatter,
                correction,
                baseline,
                baseline.members[index],
                member.change_snapshot_id,
            )
    return None


def _replace_referral_date(
    repo: BillRepository,
    formatter: GroupedStageBulletRenderer,
    correction: GroupedStageDateCorrection,
    baseline: GroupedUpdateRender,
    owner: UpdateOwner,
    snapshot_id: str,
) -> GroupedUpdateRender | None:
    snapshot = repo.get_content_snapshot(snapshot_id)
    if (
        snapshot is None
        or snapshot.kind != "change"
        or snapshot.snapshot_id != snapshot_id
        or correction.expected_date == correction.corrected_date
        or max(correction.expected_date, correction.corrected_date) > baseline.today
    ):
        return None
    change = StatusChange.model_validate(read_json(snapshot.payload_json))
    if (change.term, change.number, change.id) != (owner[0], owner[1], owner[3]):
        return None
    stages = told_stages(change.new_stages)
    selected = [
        index
        for index, stage in enumerate(stages)
        if stage.stage_type == "Referral" and stage.date == correction.expected_date
    ]
    if len(selected) != 1:
        return None
    (index,) = selected
    stage = stages[index]
    for position, other in enumerate(stages):
        if position == index or other.date is None:
            continue
        corrected = correction.corrected_date
        if (
            other.date == corrected
            or position < index
            and other.date > corrected
            or position > index
            and other.date < corrected
        ):
            return None
    before = formatter.grouped_referral_date_bullet(stage, today=baseline.today)
    after = formatter.grouped_referral_date_bullet(
        stage.model_copy(update={"date": correction.corrected_date}), today=baseline.today
    )
    entry = _member_entry(baseline, owner)
    if before is None or after is None or entry is None or before == after:
        return None
    bullets = [formatter.grouped_stage_bullet(item, today=baseline.today) for item in stages]
    lines = entry.split("\n")
    positions = [index for index, line in enumerate(lines) if line.startswith("• ")]
    if (
        not positions
        or positions != list(range(positions[0], positions[0] + len(bullets)))
        or [lines[position] for position in positions] != bullets
        or bullets[index] != before
        or after in lines
    ):
        return None
    lines[positions[index]] = after
    candidate = replace_grouped_update_member(baseline, owner, "\n".join(lines))
    if (
        candidate is None
        or preview_grouped_update(
            baseline, candidate, owner=owner, expected_sha256=correction.expected_sha256
        )
        != candidate.text
    ):
        return None
    return candidate
