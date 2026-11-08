"""Record explicit independent judgments over a pinned saved primary answer and request."""

import argparse
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lexinform.models import EvidenceCitation, ReviewDocument
from lexinform.models.physical_inventory import Sha256
from lexinform.models.reviews import citation_document_in
from tools.audit_saved_primary_claims import (
    ReviewInputCheckpoint,
    SavedClaimReview,
    _claim_slots,
    saved_claim_review,
)
from tools.corpus_artifacts import digest, read_artifact, write_json
from tools.review_saved_comparison import review_pair
from tools.sqlite_snapshot import live_journal, snapshot_files

_CLAIM_DOCUMENT_ID = "submitted-context"
_BASELINE_DOCUMENT_ID = "previous-submitted-context"
_MAX_ANSWERS_BYTES = 262_144


class LiteralReviewCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence: EvidenceCitation
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)


class ClaimJudgment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: Literal["key_changes", "affected_groups", "effective_date", "changes_since_previous"]
    index: int | None = Field(ge=0, strict=True)
    claim_sha256: Sha256
    judgment: Literal["supported", "unsupported", "unknown"]
    reason: str = Field(min_length=1, max_length=1000)
    citation: LiteralReviewCitation | None = None
    before_citation: LiteralReviewCitation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class BaselineReviewPin(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    request_sha256: Sha256
    text_sha256: Sha256
    input_checkpoint_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ReviewAnswers(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["saved-primary-claim-review-v1"]
    database_sha256: Sha256
    result_id: str = Field(pattern=r"^result-v1:[0-9a-f]{64}$")
    request_sha256: Sha256
    text_sha256: Sha256
    input_checkpoint_sha256: Sha256 | None = None
    baseline: BaselineReviewPin | None = Field(default=None, exclude_if=lambda value: value is None)

    reviewer: str = Field(min_length=1, max_length=128)
    reviewed_on: date = Field(strict=True)
    reviewer_kind: Literal["human", "synthetic_control"]
    independent: bool = Field(strict=True)
    claims: tuple[ClaimJudgment, ...] = Field(min_length=1, max_length=128)


def _validate_citation(citation: LiteralReviewCitation, source: ReviewDocument) -> None:
    if (
        citation.evidence.document_id != source.identity
        or not 0 <= citation.start < citation.end <= len(source.text)
        or not 0 < len(citation.evidence.quote) <= 1200
        or source.text[citation.start : citation.end] != citation.evidence.quote
        or citation_document_in(citation.evidence, (source,), {"project_text"}) is None
    ):
        raise ValueError("Citation differs from its exact submitted-context text span or locator")


def validate_judgment(
    item: ClaimJudgment,
    claim: str,
    source: ReviewDocument,
    baseline: ReviewDocument | None = None,
) -> None:
    if digest(claim.encode()) != item.claim_sha256 or not item.reason.strip():
        raise ValueError("Review requires the exact saved claim and a nonempty judgment reason")
    if item.field == "changes_since_previous":
        if item.judgment != "unknown" and (
            baseline is None or item.before_citation is None or item.citation is None
        ):
            raise ValueError("Comparison judgment requires literal citations from both editions")
        if item.before_citation is not None:
            if baseline is None:
                raise ValueError("Previous citation requires a verified previous edition")
            _validate_citation(item.before_citation, baseline)
    elif item.before_citation is not None:
        raise ValueError("Previous citations belong to comparisons")
    if item.citation is not None:
        _validate_citation(item.citation, source)
    elif item.judgment == "supported":
        raise ValueError("A supported judgment requires an explicit literal citation")


def _review_document(saved: SavedClaimReview, identity: str) -> ReviewDocument:
    return ReviewDocument(
        identity=identity,
        url="",
        role="project_text",
        text=saved.text,
        sha256=saved.text_sha256,
        normalized_sha256=saved.text_sha256,
        complete=False,
    )


def _review_inputs(
    database: Path,
    database_sha256: str,
    result_id: str,
    input_checkpoint: ReviewInputCheckpoint | None,
    baseline_checkpoint: ReviewInputCheckpoint | None,
    *,
    comparison: bool,
) -> tuple[BaselineReviewPin | None, ReviewDocument | None, SavedClaimReview]:
    if comparison:
        before, after = review_pair(
            database,
            database_sha256,
            result_id,
            baseline_checkpoint=baseline_checkpoint,
            input_checkpoint=input_checkpoint,
        )
        previous_id = before.record.computation_id
        if previous_id is None:
            raise ValueError("Comparison requires a retained previous result identity")
        baseline = BaselineReviewPin(
            result_id=previous_id,
            request_sha256=before.request_sha256,
            text_sha256=before.text_sha256,
            input_checkpoint_sha256=before.input_checkpoint_sha256,
        )
        return baseline, _review_document(before, _BASELINE_DOCUMENT_ID), after
    if baseline_checkpoint is not None:
        raise ValueError("A baseline checkpoint requires comparison mode")
    return (
        None,
        None,
        saved_claim_review(
            database,
            database_sha256=database_sha256,
            result_id=result_id,
            input_checkpoint=input_checkpoint,
        ),
    )


def _review_output(
    database: Path, output: Path, *checkpoints: ReviewInputCheckpoint | None
) -> Path:
    db = database.resolve(strict=True)
    protected = snapshot_files(db)
    for checkpoint in checkpoints:
        if checkpoint is not None:
            protected.update(snapshot_files(checkpoint.database))
    if output.resolve() in protected or output.is_symlink() or output.exists():
        raise ValueError("Review output must be new and separate from the input snapshot")
    return db


def template(
    database: Path,
    database_sha256: str,
    result_id: str,
    output: Path,
    *,
    input_checkpoint: ReviewInputCheckpoint | None = None,
    baseline_checkpoint: ReviewInputCheckpoint | None = None,
    comparison: bool = False,
) -> dict[str, object]:
    db = _review_output(database, output, input_checkpoint, baseline_checkpoint)
    baseline_pin, _, saved = _review_inputs(
        db, database_sha256, result_id, input_checkpoint, baseline_checkpoint, comparison=comparison
    )
    claims = [
        {
            "field": field,
            "index": index,
            "claim_sha256": digest(text.encode()),
            "judgment": None,
            "reason": None,
            "citation": None,
            **(
                {"before_citation": None}
                if baseline_pin and field == "changes_since_previous"
                else {}
            ),
        }
        for field, index, text in _claim_slots(saved.record)
        if text.strip()
    ]
    if not 1 <= len(claims) <= 128:
        raise ValueError("The saved answer has no bounded reviewable claim set")
    result: dict[str, object] = {
        "version": "saved-primary-claim-review-v1",
        "database_sha256": database_sha256,
        "result_id": result_id,
        "request_sha256": saved.request_sha256,
        "text_sha256": saved.text_sha256,
        **(
            {"input_checkpoint_sha256": saved.input_checkpoint_sha256}
            if saved.input_checkpoint_sha256 is not None
            else {}
        ),
        **({"baseline": baseline_pin.model_dump(mode="json")} if baseline_pin else {}),
        "reviewer": None,
        "reviewed_on": None,
        "reviewer_kind": None,
        "independent": None,
        "claims": claims,
    }
    write_json(output, result)
    return result


def record(
    database: Path,
    database_sha256: str,
    result_id: str,
    answers: list[tuple[Path, str]],
    output: Path,
    *,
    input_checkpoint: ReviewInputCheckpoint | None = None,
    baseline_checkpoint: ReviewInputCheckpoint | None = None,
    comparison: bool = False,
) -> dict[str, object]:
    db = _review_output(database, output, input_checkpoint, baseline_checkpoint)
    if not 1 <= len(answers) <= 16:
        raise ValueError("Supply one to sixteen pinned answer files")
    baseline_pin, baseline_source, saved = _review_inputs(
        db, database_sha256, result_id, input_checkpoint, baseline_checkpoint, comparison=comparison
    )
    source = _review_document(saved, _CLAIM_DOCUMENT_ID)
    slots = {
        (field, index): text for field, index, text in _claim_slots(saved.record) if text.strip()
    }
    reviews: list[dict[str, object]] = []
    voters: set[str] = set()
    votes: dict[tuple[str, int | None], set[str]] = {}
    for path, pin in answers:
        raw = read_artifact(path)
        if len(raw) > _MAX_ANSWERS_BYTES or digest(raw) != pin:
            raise ValueError("Answer file differs from its bounded SHA pin")
        try:
            review = ReviewAnswers.model_validate_json(raw)
        except ValidationError:
            raise ValueError("Answer file differs from the saved claim review schema") from None
        if (
            review.database_sha256,
            review.result_id,
            review.request_sha256,
            review.text_sha256,
            review.input_checkpoint_sha256,
        ) != (
            database_sha256,
            result_id,
            saved.request_sha256,
            saved.text_sha256,
            saved.input_checkpoint_sha256,
        ):
            raise ValueError("Review answers refer to a different saved answer or request")
        if review.baseline != baseline_pin:
            raise ValueError("Review answers refer to a different previous result or request")
        voter = review.reviewer.strip().casefold()
        if not voter or voter in voters or (review.reviewer_kind != "human" and review.independent):
            raise ValueError("Require one named reviewer and a valid independence declaration")
        voters.add(voter)
        seen: set[tuple[str, int | None]] = set()
        for item in review.claims:
            address = item.field, item.index
            if address in seen or address not in slots:
                raise ValueError("Require one judgment per exact saved claim slot")
            seen.add(address)
            validate_judgment(item, slots[address], source, baseline_source)
            if review.reviewer_kind == "human" and review.independent:
                votes.setdefault(address, set()).add(item.judgment)
        reviews.append(
            {
                "answers_sha256": pin,
                **review.model_dump(mode="json", exclude={"claims"}),
                "claims": [
                    {
                        **item.model_dump(mode="json"),
                        "mechanical": (
                            "literal_verified"
                            if item.citation or item.before_citation
                            else "not_supplied"
                        ),
                    }
                    for item in review.claims
                ],
            }
        )
    if (
        digest(read_artifact(db)) != database_sha256
        or live_journal(db)
        or input_checkpoint is not None
        and not input_checkpoint.unchanged()
        or baseline_checkpoint is not None
        and not baseline_checkpoint.unchanged()
        or any(digest(read_artifact(path)) != pin for path, pin in answers)
    ):
        raise ValueError("Review snapshot or pinned answers changed during recording")
    labels: list[dict[str, object]] = []
    for (field, index), text in slots.items():
        declared = votes.get((field, index), set())
        known = declared - {"unknown"}
        judgment = (
            "unreviewed"
            if not declared
            else "unknown"
            if not known
            else "conflict"
            if len(known) > 1
            else next(iter(known))
        )
        labels.append(
            {
                "field": field,
                "index": index,
                "claim_sha256": digest(text.encode()),
                "judgment": judgment,
            }
        )
    result: dict[str, object] = {
        "version": "saved-primary-claim-review-v1",
        "status": "recorded",
        "scope": "Independent judgments over the retained submitted text.",
        "database_sha256": database_sha256,
        "result_id": result_id,
        "request_sha256": saved.request_sha256,
        "text_sha256": saved.text_sha256,
        **(
            {"input_checkpoint_sha256": saved.input_checkpoint_sha256}
            if saved.input_checkpoint_sha256 is not None
            else {}
        ),
        **({"baseline": baseline_pin.model_dump(mode="json")} if baseline_pin else {}),
        "reviews": sorted(reviews, key=lambda row: str(row["answers_sha256"])),
        "claims": labels,
        "judgment_source": "reviewer_declared",
    }
    write_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--database-sha256", required=True)
    parser.add_argument("--result-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--template", action="store_true", help="write a blank pinned answer form")
    mode.add_argument("--answers", nargs=2, action="append", metavar=("PATH", "SHA256"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-checkpoint", nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--comparison", action="store_true")
    parser.add_argument("--baseline-checkpoint", nargs=2, metavar=("PATH", "SHA256"))
    args = parser.parse_args()
    input_checkpoint = (
        ReviewInputCheckpoint(Path(args.input_checkpoint[0]), args.input_checkpoint[1])
        if args.input_checkpoint
        else None
    )
    baseline_checkpoint = (
        ReviewInputCheckpoint(Path(args.baseline_checkpoint[0]), args.baseline_checkpoint[1])
        if args.baseline_checkpoint
        else None
    )
    try:
        if args.template:
            template(
                args.database,
                args.database_sha256,
                args.result_id,
                args.output,
                input_checkpoint=input_checkpoint,
                baseline_checkpoint=baseline_checkpoint,
                comparison=args.comparison,
            )
        else:
            record(
                args.database,
                args.database_sha256,
                args.result_id,
                [(Path(path), pin) for path, pin in args.answers],
                args.output,
                input_checkpoint=input_checkpoint,
                baseline_checkpoint=baseline_checkpoint,
                comparison=args.comparison,
            )
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
