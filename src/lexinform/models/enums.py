"""Enumerations and literal types shared by every other model.

A bill's number says which source it came from: `PRE_PRINT_PREFIX` for bills the Sejm has
received but not yet given a print (druk) number, `RCL_PREFIX` for government projects on
legislacja.rcl.gov.pl (keyed by the RCL project id) and `WYKAZ_PREFIX` for entries of the wykaz
prac legislacyjnych RM (keyed by their own number, UD408). A bare number is a druk.

`TextSource` says how an analysis was made: the full text of one PDF, the full text of a set of
documents (RCL: projekt + uzasadnienie + OSR), keyword excerpts (the triage rejected it) or
metadata only. `SourceKind` says which document it read, and `AMENDMENT_SOURCES` are the two that
carry no bill text but amendments — the Senate's resolution print, and the committee report that
answers amendments rather than attaching a new text. `SUPPLEMENT_SOURCES` are the documents
filed to a print after it was submitted (`PrintInfo.additional_prints`): they carry no bill text
either, but say what the government and its assessment of the effects make of the text there
already is.

`BILL_DOCUMENT_TYPE` is the Polish display string the Sejm API filters `documentType` on; the
enum value `BILL` does not filter.
"""

from enum import StrEnum
from typing import Literal


class DocumentType(StrEnum):
    BILL = "BILL"
    DRAFT_RESOLUTION = "DRAFT_RESOLUTION"
    OTHER = "OTHER"


class BillStatus(StrEnum):
    """How far a bill got through our own pipeline.

    The skips say where it stopped: `SKIPPED_PREFILTER` on the title and description, with the
    text never read; `TEXT_PREFILTER_PENDING` when the title missed but the print's text is still
    to be scanned, and `SKIPPED_TEXT_PREFILTER` when that missed too; `SKIPPED_COST` when the text
    was longer than the per-bill cost limit and `SKIPPED_CLOSED` when the road was already over
    at first sight (`lexinform reset` revives either). `LINKED` is a row that continues under
    another number — an RPW entry or an RCL project that became a print (`Bill.linked_number`).
    """

    DISCOVERED = "discovered"
    SKIPPED_PREFILTER = "skipped_prefilter"
    TEXT_PREFILTER_PENDING = "text_prefilter_pending"
    SKIPPED_TEXT_PREFILTER = "skipped_text_prefilter"
    SKIPPED_COST = "skipped_cost"
    SKIPPED_CLOSED = "skipped_closed"
    ANALYSIS_PENDING = "analysis_pending"
    ANALYSIS_FAILED = "analysis_failed"
    ANALYZED = "analyzed"
    LINKED = "linked"


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

    `AGENDA` is sent once per sitting the bill appears on, `CONSULTATION_DEADLINE` and
    `HEARING_DEADLINE` a few days before those windows close, `CONSULTATION_RESULTS` when the
    Sejm publishes the opinions it received, `IN_FORCE` on the day the act starts to apply.
    `DECISION_DEADLINE` warns that the Senate's 30 days (art. 121) or the President's 21
    (art. 122) are running out — the `ref` is the phase key, so each of the two is told once.
    `JOINT_BILL` is the short reply a bill gets instead of a card of its own when it is
    considered jointly with one that already has one; the group is followed through that card.
    """

    NEW_BILL = "new_bill"
    STATUS_UPDATE = "status_update"
    ACT_PUBLISHED = "act_published"
    IN_FORCE = "in_force"
    CONSULTATION_DEADLINE = "consultation_deadline"
    CONSULTATION_RESULTS = "consultation_results"
    AGENDA = "agenda"
    HEARING_DEADLINE = "hearing_deadline"
    DECISION_DEADLINE = "decision_deadline"
    JOINT_BILL = "joint_bill"


class PublicationStatus(StrEnum):
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
    DRY_RUN = "dry_run"


TextSource = Literal["pdf", "documents", "excerpts", "metadata_only"]
FULL_TEXT_SOURCES: frozenset[TextSource] = frozenset({"pdf", "documents"})
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
