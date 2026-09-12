"""Enumerations and literal types shared by every other model."""

from enum import StrEnum
from typing import Literal


class DocumentType(StrEnum):
    BILL = "BILL"
    DRAFT_RESOLUTION = "DRAFT_RESOLUTION"
    OTHER = "OTHER"


class BillStatus(StrEnum):
    DISCOVERED = "discovered"
    SKIPPED_PREFILTER = "skipped_prefilter"  # title/description miss, text never checked
    TEXT_PREFILTER_PENDING = "text_prefilter_pending"  # title miss; the print text is next
    SKIPPED_TEXT_PREFILTER = "skipped_text_prefilter"  # title and text miss
    SKIPPED_COST = "skipped_cost"  # text too long for the per-bill cost limit (see `reset`)
    SKIPPED_CLOSED = "skipped_closed"  # the process was already over when we first saw the bill
    ANALYSIS_PENDING = "analysis_pending"
    ANALYSIS_FAILED = "analysis_failed"
    ANALYZED = "analyzed"
    LINKED = "linked"  # a pre-print (RPW) entry that became a numbered print (Bill.linked_number)


PRE_PRINT_PREFIX = "RPW/"  # numbers of bills that have not been assigned a print (druk) number yet
RCL_PREFIX = "RCL/"  # government projects on legislacja.rcl.gov.pl, keyed by the RCL project id
WYKAZ_PREFIX = "WPL/"  # entries of the wykaz prac legislacyjnych RM, keyed by their number (UD408)
WYKAZ_REGISTER_URL = "https://www.gov.pl/web/premier/wplip-rm"  # the register itself


class Category(StrEnum):
    LEGAL_STAY = "legal_stay"
    EMPLOYMENT = "employment"
    SOCIAL = "social"
    INDIRECT = "indirect"
    MARGINAL = "marginal"
    NONE = "none"


BILL_DOCUMENT_TYPE = "projekt ustawy"  # the `documentType` display string used by the API filter


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
    NEW_BILL = "new_bill"
    STATUS_UPDATE = "status_update"
    ACT_PUBLISHED = "act_published"  # the act appeared in Dziennik Ustaw
    IN_FORCE = "in_force"  # reminder on the day the act enters into force
    CONSULTATION_DEADLINE = "consultation_deadline"  # public consultation ends in a few days
    CONSULTATION_RESULTS = "consultation_results"  # the Sejm published the opinions received
    AGENDA = "agenda"  # the bill is on the agenda of a committee or Sejm sitting (one per sitting)
    HEARING_DEADLINE = "hearing_deadline"  # applications to a public hearing close in a few days
    # A bill considered jointly with one that already has a card: a short reply under that card
    # instead of a card of its own (the group is followed through the card's bill)
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


# How an analysis was made: the full text of one PDF, the full text of a set of documents (RCL:
# projekt + uzasadnienie + OSR), keyword excerpts (rejected by the triage), or metadata only.
TextSource = Literal["pdf", "documents", "excerpts", "metadata_only"]
FULL_TEXT_SOURCES: frozenset[TextSource] = frozenset({"pdf", "documents"})
SourceKind = Literal[
    "print",
    "committee_report",
    "text_after3",
    "rcl",
    "metadata",
    # Amendments only, not a bill text: the Senate's resolution print, the additional ("-A")
    # committee report on 2nd-reading amendments, the report on the Senate's position.
    "senate_amendments",
    "committee_amendments",
]
AMENDMENT_SOURCES: frozenset[SourceKind] = frozenset({"senate_amendments", "committee_amendments"})
