"""Enumerations and literal types shared by every other model.

A bill's number says its source: `PRE_PRINT_PREFIX` before a druk number, `RCL_PREFIX` for a
government project, `WYKAZ_PREFIX` for a register entry. A bare number is a druk.

`TextSource` says how an analysis was made, `SourceKind` which document it read.
`AMENDMENT_SOURCES` carry amendments and no bill text; `SUPPLEMENT_SOURCES` are filed afterwards
and say what others make of the text. `BILL_DOCUMENT_TYPE` is the display string the API filters
`documentType` on; the enum value `BILL` does not filter.
"""

from enum import StrEnum
from typing import Literal


class DocumentType(StrEnum):
    BILL = "BILL"
    DRAFT_RESOLUTION = "DRAFT_RESOLUTION"
    OTHER = "OTHER"


class VetoOutcome(StrEnum):
    PENDING = "pending"
    OVERRIDDEN = "overridden"
    SUSTAINED = "sustained"


class BillStatus(StrEnum):
    """How far a bill got through our own pipeline.

    The skips say where it stopped — on the title, on the text, on the per-bill cost limit, or on
    a road already over at first sight — and `reset`/`/unskip` revive any of them. `LINKED`
    continues under another number (`Bill.linked_number`). A `SKIPPED_JOINT` is gone with the rule
    that set it; v21 turns such a row into `ANALYSIS_PENDING`. `BATCH_PENDING` is `ANALYSIS_PENDING`
    once submitted: the request is with the provider, not yet collected (v26).
    """

    DISCOVERED = "discovered"
    SKIPPED_PREFILTER = "skipped_prefilter"
    TEXT_PREFILTER_PENDING = "text_prefilter_pending"
    SKIPPED_TEXT_PREFILTER = "skipped_text_prefilter"
    SKIPPED_COST = "skipped_cost"
    SKIPPED_CLOSED = "skipped_closed"
    ANALYSIS_PENDING = "analysis_pending"
    BATCH_PENDING = "batch_pending"
    REANALYSIS_READY = "reanalysis_ready"
    ANALYSIS_FAILED = "analysis_failed"
    ANALYZED = "analyzed"
    LINKED = "linked"


SILENCED_BY_OPERATOR = "silenced by the operator (/skip)"
"""The reason `/skip` writes: the one `skipped_prefilter` that is a decision and not a miss, which
nothing revisiting skips in bulk may undo. The status cannot say it — `/skip` reuses that one."""

PRE_PRINT_PREFIX = "RPW/"
RCL_PREFIX = "RCL/"
WYKAZ_PREFIX = "WPL/"
WYKAZ_REGISTER_URL = "https://www.gov.pl/web/premier/wplip-rm"


class Category(StrEnum):
    LEGAL_STAY = "legal_stay"
    EMPLOYMENT = "employment"
    SOCIAL = "social"
    INDIRECT = "indirect"
    MARGINAL = "marginal"
    NONE = "none"


BILL_DOCUMENT_TYPE = "projekt ustawy"


class ApplicantType(StrEnum):
    GOVERNMENT = "government"
    DEPUTIES = "deputies"
    SENATE = "senate"
    PRESIDENT = "president"
    PRESIDIUM = "presidium"
    CITIZENS = "citizens"
    COMMITTEE = "committee"
    UNKNOWN = "unknown"


class PublicationKind(StrEnum):
    """What a Telegram post is: the card of a new bill, or one of the replies under it.

    `AGENDA` is sent once per sitting and `AGENDA_CANCELLED` retracts one under the same `ref`
    (a sitting that only moved keeps its key and is told as a new `AGENDA`). `DECISION_DEADLINE`
    warns the Senate's 30 days or the President's 21 are running out, its `ref` the phase key so
    each is told once. `JOINT_BILL` replaces the card of a jointly considered print. `DIGEST`
    is the one kind about no bill: its `ref` is the ISO week.
    """

    NEW_BILL = "new_bill"
    STATUS_UPDATE = "status_update"
    ACT_PUBLISHED = "act_published"
    IN_FORCE = "in_force"
    CONSULTATION_DEADLINE = "consultation_deadline"
    CONSULTATION_RESULTS = "consultation_results"
    AGENDA = "agenda"
    AGENDA_CANCELLED = "agenda_cancelled"
    HEARING_DEADLINE = "hearing_deadline"
    DECISION_DEADLINE = "decision_deadline"
    JOINT_BILL = "joint_bill"
    DIGEST = "digest"


class PublicationStatus(StrEnum):
    QUEUED = "queued"
    PENDING = "pending"
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RunMode(StrEnum):
    """What a recorded run was: the whole day, one phase, or a rehearsal of either."""

    RUN = "run"
    TRACK = "track"
    COMMANDS = "commands"
    COLLECT = "collect"
    DRY_RUN = "dry_run"


TextSource = Literal["pdf", "documents", "scan", "excerpts", "metadata_only"]
FULL_TEXT_SOURCES: frozenset[TextSource] = frozenset({"pdf", "documents", "scan"})
SourceKind = Literal[
    "print",
    "committee_report",
    "text_after3",
    "rcl",
    "metadata",
    "senate_amendments",
    "committee_amendments",
    "government_position",
    "impact_assessment",
]
AMENDMENT_SOURCES: frozenset[SourceKind] = frozenset({"senate_amendments", "committee_amendments"})
SUPPLEMENT_SOURCES: frozenset[SourceKind] = frozenset({"government_position", "impact_assessment"})
