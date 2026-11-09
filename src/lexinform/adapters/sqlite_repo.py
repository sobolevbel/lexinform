"""SQLite implementation of BillRepository (stdlib sqlite3, no ORM).

Schema is versioned with PRAGMA user_version; `migrate()` applies missing steps in order.
`dump()` / `restore()` move the whole database through a text SQL script so the state can live in
a git branch with readable diffs.
"""

import gzip
import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from lexinform.adapters.amendment_inputs import (
    capture_submitted_amendment_scan_input,
    checked_computation_result,
    prune_amendment_inputs,
    read_amendment_input,
    read_amendment_scan_context,
    validate_amendment_inputs,
    validate_intent_input,
    validate_item_input,
    validate_result_input,
    write_amendment_scan_context,
    write_shared_amendment_input,
)
from lexinform.adapters.candidate_trace_history import (
    read_candidate_disposition_trace,
    validate_candidate_disposition_traces,
    validate_candidate_trace_link,
    validate_candidate_trace_links,
)
from lexinform.adapters.computation_history import read_result_application
from lexinform.adapters.content_applications import (
    find_projection_application_ids,
    find_supplement_content_evidence,
    verify_content_applications,
)
from lexinform.adapters.content_history import (
    CardContentInventory,
    card_primary_evidence_candidate,
    card_publication_rows,
    card_refresh_rows,
    read_card_content_inventory,
    read_content_version,
    read_grouped_stage_telegram_target,
    recorded_command_matches,
    validate_content_history,
    verify_act_published_content,
    verify_card_content,
    verify_direct_stage_content,
    verify_grouped_stage_content,
    verify_multipart_card_revision,
    verify_original_delivery_content,
    verify_sitting_content,
)
from lexinform.adapters.content_source_refs import (
    ContentSourceRows,
    validate_content_sources,
    validate_projected_application,
    validate_projected_applications,
    verify_content_source,
)
from lexinform.adapters.digest_content import ResolvedDigestRenderedContent, verify_digest_content
from lexinform.adapters.document_source_rows import (
    DocumentSourceRows,
    validate_review_source_observations,
)
from lexinform.adapters.document_work import (
    read_document_work,
    validate_document_work,
    validate_revision_application_work,
    validate_senate_input_witness,
)
from lexinform.adapters.extraction_history import (
    ExtractionResultRowError,
    read_extraction_result,
    validate_extraction_results,
)
from lexinform.adapters.input_receipts import (
    capture_frozen_request_manifest,
    capture_submitted_supplement_scan_input,
    intent_metadata,
    parse_batch_request,
    serialize_intent_metadata,
    serialize_intent_request,
    validate_input_snapshots,
    validate_multipart_intent,
    validate_multipart_item,
    validate_scan_attachment,
    validate_word_formula_intent,
)
from lexinform.adapters.joint_content_evidence import joint_comparison_application_id
from lexinform.adapters.legacy_history import read_legacy_batch_item_meta
from lexinform.adapters.legacy_history import (
    read_legacy_result as _read_legacy_result,
)
from lexinform.adapters.legacy_history import (
    validate_legacy_import as _validate_legacy_import,
)
from lexinform.adapters.legacy_history import (
    validate_legacy_projections as _validate_legacy_projections,
)
from lexinform.adapters.memo_retention import (
    batch_payload_cleanup_filter,
    batch_retention_cte,
    memo_retention_cte,
)
from lexinform.adapters.physical_history import (
    PHYSICAL_INVENTORY_MIGRATION,
    PhysicalInventoryReader,
    physical_recorded_at,
    validate_physical_inventory,
)
from lexinform.adapters.primary_claim_receipts import (
    CheckedPrimaryClaimReceipt,
    CheckedPrimaryComparisonReceipt,
    read_primary_claim_receipts,
    validate_primary_claim_receipts,
)
from lexinform.adapters.primary_projections import (
    PrimaryProjectionReader,
    validate_primary_cost_projections,
    validate_primary_projections,
)
from lexinform.adapters.review_content_evidence import direct_review_application_id
from lexinform.adapters.review_memos import validate_review_memo_row, validate_review_memos
from lexinform.adapters.review_prepared_input import capture_submitted_review_input
from lexinform.adapters.secondary_dependencies import (
    validate_secondary_dependencies,
    validate_secondary_dependency,
)
from lexinform.adapters.senate_history import (
    SenateInspectionRowError,
    read_senate_inspection,
    senate_inspection_links,
    validate_senate_inspections,
    verify_senate_inspection,
)
from lexinform.adapters.source_catalog_attempts import (
    ATTEMPT_COLUMNS,
    CATALOG_COLUMNS,
    SOURCE_CATALOG_MIGRATION,
    SourceCatalogRowError,
    attempt_values,
    catalog_values,
    read_attempt,
    read_catalog,
    validate_catalog_attempts,
)
from lexinform.adapters.source_omissions import (
    SOURCE_OMISSION_MIGRATION,
    SOURCE_REFERENCE_COLUMNS,
    SOURCE_REFERENCE_OMISSION_MIGRATION,
    SourceOmissionRowError,
    omission_values,
    read_source_omission,
    validate_source_omissions,
)
from lexinform.adapters.status_changes import read_status_change
from lexinform.adapters.supplement_dependencies import (
    validate_status_supplement_dependencies,
    validate_supplement_dependencies,
)
from lexinform.content_sources import (
    SOURCE_CONTEXT_INVALID,
    SourceContextInvalidError,
    inspect_primary_content_sources,
    source_context_blocked,
    supported_source_context,
)
from lexinform.content_versions import (
    GROUPED_STAGE_REVISION_REF_PREFIX,
    confirmed_content_version,
    content_delivery_mismatch,
    content_snapshot_refs,
    grouped_stage_revision_plan,
    grouped_stage_revision_ref,
    is_grouped_stage_revision_slot,
)
from lexinform.direct_delivery import direct_root_plan
from lexinform.document_editions import candidate_edition
from lexinform.errors import StateCheckpointError
from lexinform.json_storage import compact_json, storage_json
from lexinform.legacy_delivery import legacy_digest_root
from lexinform.legacy_maintenance import (
    LegacyMaintenancePlan,
    LegacyMaintenanceResult,
    LegacySnapshotColumn,
    plan_legacy_maintenance,
)
from lexinform.legacy_results import (
    LEGACY_PROJECTION_COLUMNS,
    preserve_legacy_json,
    read_legacy_json,
)
from lexinform.log_context import LogFields, log_extra
from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    PRE_PRINT_PREFIX,
    RCL_PREFIX,
    WYKAZ_PREFIX,
    ActInfo,
    AgendaItem,
    AmendmentInputReceipt,
    AmendmentRevisionApplicationWork,
    AmendmentsRecord,
    AnalysisRecord,
    ArtifactMetadata,
    ArtifactReadReceipt,
    AttentionAssessment,
    BatchIntent,
    BatchItemMeta,
    BatchProvider,
    BatchResult,
    BatchStatus,
    Bill,
    BillAuthors,
    BillStatus,
    BillSubmission,
    CandidateDispositionTrace,
    CheckAspect,
    CommandState,
    ComputationResult,
    ContentSnapshot,
    ContentVersion,
    DeliveryAlert,
    DeliveryPlan,
    DeliveryResolution,
    DigestItemMeta,
    DigestPart,
    DocumentEdition,
    DocumentReviewRecord,
    DocumentSelectionRecord,
    DocumentWork,
    Editorial,
    ExtractionResult,
    IncomingCommand,
    JointRecord,
    LegacyResultImport,
    LegacyResultOccurrence,
    LegacyResultValue,
    LlmBatch,
    LlmBatchItem,
    Mnemonic,
    ObservationBasis,
    ObservationMode,
    ObservedProcess,
    PrimaryInputReceipt,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    RclLetterUse,
    RclLetterUseRecord,
    RclProject,
    ReadyAnalysis,
    ResultApplicabilityObservation,
    ResultApplication,
    ResultApplicationTarget,
    ReviewSource,
    ReviewWork,
    RunMode,
    RunReport,
    SelectionArtifactReference,
    SelectionEditionReference,
    SelectionExtractionReference,
    SenateAct,
    SourceCheck,
    SourceObservation,
    Stage,
    StatusChange,
    SupplementInputReceipt,
    SupplementRecord,
    WykazEntry,
    card_edit_owner_id,
    failed_card_part_resolution_allowed,
    rcl_num_key,
    valid_mnemonic,
)
from lexinform.models.amendment_inputs import AmendmentInputSnapshot, AmendmentScanInputWitness
from lexinform.models.batch import BatchJob, BatchKind
from lexinform.models.commands import (
    GroupedEditDeliveryResolution,
    GroupedEditResolutionEnvelope,
    read_delivery_resolution,
)
from lexinform.models.content import (
    CardPrimaryEvidence,
    SupplementContentEvidence,
    editorial_bill_snapshot,
    pack_original_bill_snapshot,
)
from lexinform.models.digest_content import RENDERED_DIGEST_PROFILES
from lexinform.models.legacy_results import canonical_legacy_json, legacy_json
from lexinform.models.physical_inventory import PhysicalCapture, PhysicalMetadata
from lexinform.models.rcl import rcl_number
from lexinform.models.senate_inspection import SenateInspectionLink, SenateInspectionRecord
from lexinform.models.sitting_content import (
    MAX_SITTING_HISTORY_BYTES,
    MAX_SITTING_HISTORY_ROWS,
    SittingContentMember,
    SittingContentPart,
    SittingContentPlan,
    sitting_content_context,
)
from lexinform.models.source_catalog_attempts import SourceCatalogAttempt, SourceCatalogSnapshot
from lexinform.models.source_omissions import SourceMetadataOmission
from lexinform.models.wykaz import wykaz_number
from lexinform.name_binding import bind_mnemonic
from lexinform.ports import (
    CardContentHistory,
    GroupedEditClaim,
    VerifiedEditOutcome,
    VerifiedEditRejection,
)
from lexinform.primary_projections import ProjectionOwner
from lexinform.saved_records import CONTEXT_RESULT_TABLES
from lexinform.sitting_delivery import sitting_root_plan
from lexinform.telegram_text import MESSAGE_LIMIT, split_html, utf16_length

_STAGES = TypeAdapter(tuple[Stage, ...])
_COMPACT_SNAPSHOTS: dict[str, Callable[[str], str]] = {
    "summary_json": lambda raw: compact_json(raw, ProcessSummary.model_validate_json(raw)),
    "rcl_json": lambda raw: compact_json(raw, RclProject.model_validate_json(raw)),
    "observed_process_json": lambda raw: compact_json(
        raw, ObservedProcess.model_validate_json(raw)
    ),
    "stages_json": lambda raw: compact_json(raw, _STAGES.validate_json(raw)),
}

_NO_CARD_YET = """
                  NOT EXISTS (
                      SELECT 1 FROM publications p
                      WHERE p.term = b.term AND p.number = b.number
                        AND p.kind IN ('new_bill', 'joint_bill')
                        AND p.channel_id = ?
                        AND (p.status IN ('sent', 'skipped', 'pending', 'unknown', 'dismissed')
                             OR (p.status = 'failed' AND p.attempts >= ?))
                  )"""
"""The bill has no post in this channel yet: no card, no reply, and no failed attempt left to
retry. Both candidate queries take `channel_id` and `max_attempts` where it appears."""


MIGRATIONS: tuple[str, ...] = (
    # v1
    """
    CREATE TABLE bills (
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        title TEXT NOT NULL,
        change_date TEXT NOT NULL,
        closure_date TEXT,
        passed INTEGER,
        status TEXT NOT NULL,
        prefilter_hits TEXT NOT NULL DEFAULT '[]',
        summary_json TEXT NOT NULL,
        stages_json TEXT,
        stages_fingerprint TEXT,
        analysis_json TEXT,
        analysis_attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        first_seen_at TEXT NOT NULL,
        last_checked_at TEXT NOT NULL,
        PRIMARY KEY (term, number)
    );
    CREATE INDEX ix_bills_status ON bills(status);
    CREATE INDEX ix_bills_change_date ON bills(change_date);

    CREATE TABLE status_changes (
        id INTEGER PRIMARY KEY,
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        old_fingerprint TEXT,
        new_fingerprint TEXT NOT NULL,
        new_stages_json TEXT NOT NULL,
        closure_detected INTEGER NOT NULL DEFAULT 0,
        passed INTEGER,
        detected_at TEXT NOT NULL,
        UNIQUE (term, number, new_fingerprint)
    );

    CREATE TABLE publications (
        id INTEGER PRIMARY KEY,
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id INTEGER,
        document_message_ids TEXT NOT NULL DEFAULT '[]',
        status_change_id INTEGER REFERENCES status_changes(id),
        created_at TEXT NOT NULL,
        sent_at TEXT,
        error TEXT
    );
    CREATE UNIQUE INDEX ux_pub_new ON publications(term, number, channel_id)
        WHERE kind = 'new_bill';
    CREATE UNIQUE INDEX ux_pub_change ON publications(status_change_id, channel_id)
        WHERE kind = 'status_update';
    CREATE INDEX ix_pub_status ON publications(status);

    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        since TEXT NOT NULL,
        mode TEXT NOT NULL,
        ok INTEGER,
        report_json TEXT
    );
    """,
    # v2: retry bookkeeping for failed posts, re-analysis flag on status changes, watermark on runs
    """
    ALTER TABLE status_changes ADD COLUMN content_changed INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE publications ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE runs ADD COLUMN discovery_ok INTEGER;
    UPDATE runs SET discovery_ok = ok;
    """,
    # v3: bills submitted but not yet numbered (RPW) and their link to the later print
    """
    ALTER TABLE bills ADD COLUMN submission_json TEXT;
    ALTER TABLE bills ADD COLUMN linked_number TEXT;
    ALTER TABLE status_changes ADD COLUMN withdrawn INTEGER NOT NULL DEFAULT 0;
    """,
    # v4: the published act (Dziennik Ustaw) and its entry into force
    """
    ALTER TABLE bills ADD COLUMN act_json TEXT;
    ALTER TABLE bills ADD COLUMN entry_into_force TEXT;
    CREATE INDEX ix_bills_entry_into_force ON bills(entry_into_force);
    CREATE UNIQUE INDEX ux_pub_once_per_kind ON publications(term, number, kind, channel_id)
        WHERE kind IN ('act_published', 'in_force');
    """,
    # v5: signatories of deputies' bills, by club
    """
    ALTER TABLE bills ADD COLUMN authors_json TEXT;
    """,
    # v6: one consultation-deadline reminder per bill and channel
    """
    CREATE UNIQUE INDEX ux_pub_consultation ON publications(term, number, kind, channel_id)
        WHERE kind = 'consultation_deadline';
    """,
    # v7: upcoming sittings per bill; posts that recur per sitting carry a `ref`; one
    # consultation-results notice per bill and channel
    """
    ALTER TABLE bills ADD COLUMN agenda_json TEXT;
    ALTER TABLE publications ADD COLUMN ref TEXT;
    CREATE UNIQUE INDEX ux_pub_consultation_results
        ON publications(term, number, kind, channel_id) WHERE kind = 'consultation_results';
    CREATE UNIQUE INDEX ux_pub_agenda ON publications(term, number, kind, channel_id, ref)
        WHERE kind = 'agenda';
    """,
    # v8: government projects followed on RCL before the Sejm (`RCL/{id}` rows)
    """
    ALTER TABLE bills ADD COLUMN rcl_json TEXT;
    """,
    # v9: bills that lapsed with the end of a Sejm term (zasada dyskontynuacji)
    """
    ALTER TABLE bills ADD COLUMN discontinued_at TEXT;
    ALTER TABLE status_changes ADD COLUMN discontinued INTEGER NOT NULL DEFAULT 0;
    """,
    # v10: a print that continues an RCL project's thread keeps the project's wykaz number, so
    # its replies carry the card's tag (#RCL_UC104) next to their own
    """
    ALTER TABLE bills ADD COLUMN linked_wykaz_number TEXT;
    """,
    # v11: one hearing reminder per bill, channel and hearing (`ref` = the hearing date); the
    # summary of amendments (Senate, "-A" report) a status change carries
    """
    CREATE UNIQUE INDEX ux_pub_hearing ON publications(term, number, kind, channel_id, ref)
        WHERE kind = 'hearing_deadline';
    ALTER TABLE status_changes ADD COLUMN amendments_json TEXT;
    """,
    # v12: one "alternative bill" reply per bill and channel (a bill considered jointly with one
    # that already has a card joins that card's thread instead of getting its own)
    """
    CREATE UNIQUE INDEX ux_pub_joint ON publications(term, number, kind, channel_id)
        WHERE kind = 'joint_bill';
    """,
    # v13: operator commands from the technical channel, one row per Telegram update, recorded
    # before the command runs so that a re-read inbox file is not executed twice
    """
    CREATE TABLE commands (
        update_id INTEGER PRIMARY KEY,
        chat_id TEXT NOT NULL,
        message_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        received_at TEXT NOT NULL,
        handled_at TEXT,
        reply TEXT
    );
    """,
    # v14: the moment a command's side effects were done, separate from the moment it was
    # answered. A run that could not answer must repeat the answer, not the command.
    """
    ALTER TABLE commands ADD COLUMN executed_at TEXT;
    UPDATE commands SET executed_at = handled_at WHERE handled_at IS NOT NULL;
    """,
    # v15: bills the government has only announced, in the wykaz prac legislacyjnych RM
    # (`WPL/UD408` rows), months before a text exists
    """
    ALTER TABLE bills ADD COLUMN wykaz_json TEXT;
    """,
    # v16: the digest of the card as it was last rendered, so that a run can tell whether the
    # card still says what it would say today without asking Telegram.
    """
    ALTER TABLE publications ADD COLUMN rendered_sha256 TEXT;
    """,
    # v17: one reminder per bill, channel and constitutional term (`ref` = the phase key), so the
    # Senate's 30 days and the President's 21 are each told once
    """
    CREATE UNIQUE INDEX ux_pub_decision ON publications(term, number, kind, channel_id, ref)
        WHERE kind = 'decision_deadline';
    """,
    # v18: the documents filed to a print after its submission (the API's `additionalPrints`):
    # which of them the channel already knows about, and the digests of those one update told
    """
    ALTER TABLE bills ADD COLUMN supplements_json TEXT;
    ALTER TABLE status_changes ADD COLUMN supplements_json TEXT;
    """,
    # v19: one retraction per bill, channel and sitting (`ref` = the announced sitting, as the
    # `agenda` post keys it), so a sitting that is called off is taken back exactly once
    """
    CREATE UNIQUE INDEX ux_pub_agenda_cancelled
        ON publications(term, number, kind, channel_id, ref)
        WHERE kind = 'agenda_cancelled';
    """,
    # v20: how a print considered jointly with others differs from them, as the reply under
    # their card says it — stored so that a retry, a `/preview` and a `/republish` do not pay
    # for the same comparison again.
    """
    ALTER TABLE bills ADD COLUMN joint_json TEXT;
    """,
    # v21: `skipped_joint` is gone with the rule that set it — a print considered jointly with
    # one that holds the card is read like any other now. A dump written before this carries the
    # word, and `BillStatus` no longer has it, so the row is put back in the queue rather than
    # failing to load.
    """
    UPDATE bills SET status = 'analysis_pending', analysis_attempts = 0
        WHERE status = 'skipped_joint';
    """,
    # v22: which RCL project carries which number of the wykaz prac RM, for every row of the
    # listing and not only for the projects this bot follows — the join that tells a plan still
    # worth a card from one whose project has been public for a year. The number alone does not
    # identify the project (the register reuses its numbers: UD368 named a CPK project in 2018
    # and a Karta Polaka plan in 2026), so the row keeps the project's creation date and a plan
    # only takes a project created on or after the day the plan was announced.
    """
    CREATE TABLE rcl_wykaz_numbers (
        wykaz_number TEXT NOT NULL,
        project_id INTEGER NOT NULL,
        created TEXT,
        PRIMARY KEY (wykaz_number, project_id)
    );
    CREATE INDEX ix_rcl_wykaz_numbers_number ON rcl_wykaz_numbers(wykaz_number);
    """,
    # v23: the weekly digest, one per week and channel (the draft in the technical channel,
    # the published one in the reader's), keyed on the week because it is about no bill. The
    # second index is the one the digest's "posts of this week" query actually takes: on
    # `sent_at` alone SQLite preferred `ix_pub_status` and never touched it.
    """
    CREATE UNIQUE INDEX ux_pub_digest ON publications(channel_id, kind, ref)
        WHERE kind = 'digest';
    CREATE INDEX ix_pub_channel_sent ON publications(channel_id, status, sent_at);
    """,
    """
    ALTER TABLE bills ADD COLUMN observed_closure_date TEXT;
    UPDATE bills SET observed_closure_date = closure_date WHERE stages_fingerprint IS NOT NULL;
    """,
    """
    ALTER TABLE bills ADD COLUMN observed_process_json TEXT;
    ALTER TABLE publications ADD COLUMN delivery_json TEXT;
    ALTER TABLE status_changes ADD COLUMN consultation_opened INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE analysis_memo (key TEXT PRIMARY KEY, record_json TEXT NOT NULL);
    """,
    # v26: analyze/reanalyze move to the provider's batch API (50% off); one llm_batches row per
    # submission, llm_batch_items the bill each custom_id answers for and whether it's collected.
    """
    CREATE TABLE llm_batches (
        batch_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        call_kind TEXT NOT NULL,
        submitted_at TEXT NOT NULL,
        status TEXT NOT NULL,
        polled_at TEXT,
        completed_at TEXT,
        request_count INTEGER NOT NULL,
        estimated_cost_usd REAL NOT NULL
    );

    CREATE TABLE llm_batch_items (
        batch_id TEXT NOT NULL REFERENCES llm_batches(batch_id),
        custom_id TEXT NOT NULL,
        call_kind TEXT NOT NULL,
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        meta_json TEXT NOT NULL,
        consumed_at TEXT,
        PRIMARY KEY (batch_id, custom_id)
    );
    CREATE INDEX ix_llm_batch_items_bill ON llm_batch_items(term, number);
    """,
    """
    CREATE TABLE llm_batch_intents (
        custom_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        request_json TEXT NOT NULL,
        meta_json TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX ix_llm_batch_intents_state ON llm_batch_intents(state, created_at);
    """,
    """
    ALTER TABLE bills ADD COLUMN analysis_generation INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE bills ADD COLUMN ready_analysis_json TEXT;
    """,
    """
    ALTER TABLE llm_batch_items ADD COLUMN result_json TEXT;
    ALTER TABLE llm_batch_items ADD COLUMN accounted_at TEXT;
    """,
    # v30: a collected batch deleted at the provider, so "ended and still listed" means uncollected.
    """
    ALTER TABLE llm_batches ADD COLUMN forgotten_at TEXT;
    """,
    # v31: the act's page on senat.gov.pl — its committees and their sittings.
    """
    ALTER TABLE bills ADD COLUMN senate_json TEXT;
    """,
    """
    ALTER TABLE bills ADD COLUMN awaiting_batch_since TEXT;
    CREATE INDEX ix_llm_batch_items_custom ON llm_batch_items(custom_id);
    """,
    """
    ALTER TABLE analysis_memo ADD COLUMN term INTEGER;
    ALTER TABLE analysis_memo ADD COLUMN number TEXT;
    ALTER TABLE analysis_memo ADD COLUMN last_used_at TEXT;
    CREATE INDEX ix_analysis_memo_retention ON analysis_memo(last_used_at);
    """,
    """
    CREATE TABLE delivery_resolutions (
        id INTEGER PRIMARY KEY,
        publication_id INTEGER NOT NULL,
        command_update_id INTEGER NOT NULL UNIQUE,
        resolution_json TEXT NOT NULL
    );
    CREATE INDEX ix_delivery_resolutions_publication ON delivery_resolutions(publication_id);
    CREATE TABLE publication_sequence (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_id INTEGER NOT NULL
    );
    INSERT INTO publication_sequence VALUES (1, (SELECT COALESCE(MAX(id), 0) FROM publications));
    """,
    # v35: observation independent of a Telegram thread; NULL mode on an old row means unknown.
    """
    ALTER TABLE bills ADD COLUMN observation_mode TEXT;
    ALTER TABLE bills ADD COLUMN observation_basis TEXT;
    CREATE TABLE source_checks (
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        aspect TEXT NOT NULL,
        last_success_at TEXT,
        last_attempt_at TEXT,
        failures INTEGER NOT NULL DEFAULT 0,
        last_outage_at TEXT,
        PRIMARY KEY (term, number, aspect)
    );
    """,
    # v36: editorial names belong to the case, independently of analysis revisions.
    """
    ALTER TABLE bills ADD COLUMN editorial_json TEXT;
    """,
    # v37: preserve priority evidence after sent payload retention.
    """
    ALTER TABLE publications ADD COLUMN attention_json TEXT;
    UPDATE publications SET attention_json = json_extract(delivery_json, '$.attention')
      WHERE delivery_json IS NOT NULL;
    """,
    # v38: the last confirmed operator alert cannot be derived from delivery status.
    """
    CREATE TABLE delivery_alerts (
        channel_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        notified_at TEXT NOT NULL
    );
    """,
    """
    ALTER TABLE status_changes ADD COLUMN decision_changed INTEGER NOT NULL DEFAULT 0;
    """,
    """
    CREATE TABLE digest_event_links (
        source_publication_id INTEGER PRIMARY KEY REFERENCES publications(id),
        owner_publication_id INTEGER NOT NULL REFERENCES publications(id)
    );
    CREATE INDEX ix_digest_owner ON digest_event_links(owner_publication_id);
    """,
    """
    ALTER TABLE bills ADD COLUMN consultation_review_json TEXT;
    ALTER TABLE bills ADD COLUMN act_application_review_json TEXT;
    ALTER TABLE bills ADD COLUMN review_work_json TEXT;
    CREATE UNIQUE INDEX ux_pub_review ON publications(term, number, kind, channel_id, ref)
      WHERE kind IN ('consultation_review', 'act_application', 'effective_rules');
    """,
    """
    CREATE UNIQUE INDEX ux_pub_message_part ON publications(channel_id, kind, ref)
      WHERE kind = 'message_part';
    """,
    """
    CREATE TABLE effective_rule_links (
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        rule_ref TEXT NOT NULL,
        publication_id INTEGER NOT NULL REFERENCES publications(id),
        PRIMARY KEY (term, number, channel_id, rule_ref)
    );
    """,
    """
    UPDATE bills SET status = 'text_prefilter_pending'
    WHERE number LIKE 'RPW/%' AND status = 'skipped_text_prefilter'
      AND last_error LIKE 'text prefilter: OrkaUnreachableError:%: HTTP 404'
      AND analysis_json IS NULL AND linked_number IS NULL AND discontinued_at IS NULL;
    """,
    """
    CREATE UNIQUE INDEX ux_pub_amendments_revision ON publications(channel_id, kind, ref)
      WHERE kind = 'amendments_revision';
    """,
    """
    CREATE TABLE document_work (
        key TEXT PRIMARY KEY,
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        state TEXT NOT NULL,
        next_at TEXT NOT NULL,
        work_json TEXT NOT NULL
    );
    CREATE INDEX ix_document_work_due ON document_work(state, next_at);
    """,
    """
    ALTER TABLE bills ADD COLUMN commission_review_json TEXT;
    CREATE UNIQUE INDEX ux_pub_commission_review ON publications(term, number, channel_id, ref)
      WHERE kind = 'commission_review';
    """,
    """
    ALTER TABLE bills ADD COLUMN committee_documents_json TEXT;
    """,
    """
    CREATE TABLE computation_results (
        result_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        custom_id TEXT NOT NULL,
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        result_json TEXT NOT NULL,
        UNIQUE (provider, batch_id, custom_id)
    );
    CREATE INDEX ix_computation_owner ON computation_results(term, number);
    CREATE TABLE result_applications (
        result_id TEXT NOT NULL REFERENCES computation_results(result_id),
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        generation INTEGER NOT NULL,
        phase TEXT NOT NULL,
        application_json TEXT NOT NULL,
        PRIMARY KEY (result_id, term, number, generation, phase)
    );
    CREATE INDEX ix_result_application_owner ON result_applications(term, number, generation);
    """,
    """
    CREATE TABLE result_applications_v50 (
        id INTEGER PRIMARY KEY,
        result_id TEXT NOT NULL REFERENCES computation_results(result_id),
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        generation INTEGER,
        phase TEXT NOT NULL,
        application_json TEXT NOT NULL,
        CHECK (
            (generation IS NOT NULL
             AND COALESCE(json_type(application_json, '$.target'), 'null') = 'null')
            OR (generation IS NULL AND phase = 'projection'
                AND json_type(application_json, '$.target') IS 'object'
                AND json_type(application_json, '$.target.kind') IS 'text'
                AND length(json_extract(application_json, '$.target.kind')) > 0
                AND json_type(application_json, '$.target.ref') IS 'text'
                AND length(json_extract(application_json, '$.target.ref')) > 0)
        )
    );
    INSERT INTO result_applications_v50
      (id, result_id, term, number, generation, phase, application_json)
      SELECT rowid, result_id, term, number, generation, phase, application_json
      FROM result_applications ORDER BY rowid;
    DROP TABLE result_applications;
    ALTER TABLE result_applications_v50 RENAME TO result_applications;
    CREATE UNIQUE INDEX ux_result_application_generation
      ON result_applications(result_id, term, number, generation, phase)
      WHERE generation IS NOT NULL;
    CREATE UNIQUE INDEX ux_result_application_target
      ON result_applications(result_id, term, number, phase,
          json_extract(application_json, '$.target.kind'),
          json_extract(application_json, '$.target.ref'))
      WHERE generation IS NULL;
    CREATE INDEX ix_result_application_owner ON result_applications(term, number, generation);
    """,
    """
    CREATE TABLE result_applicability (
        observation_id TEXT PRIMARY KEY NOT NULL,
        result_id TEXT NOT NULL REFERENCES computation_results(result_id),
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        observation_json TEXT NOT NULL
    );
    CREATE INDEX ix_result_applicability_owner ON result_applicability(term, number);
    """,
    """
    CREATE TABLE document_selections (
        selection_id TEXT PRIMARY KEY NOT NULL,
        question TEXT NOT NULL,
        policy_version TEXT,
        state TEXT NOT NULL,
        record_json TEXT NOT NULL
    );
    CREATE TABLE source_observations (
        observation_id TEXT PRIMARY KEY NOT NULL,
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        purpose TEXT NOT NULL CHECK(purpose IN ('prefilter', 'analysis', 'review')),
        selection_id TEXT NOT NULL REFERENCES document_selections(selection_id),
        source_url TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        observation_json TEXT NOT NULL
    );
    CREATE INDEX ix_source_observations_owner ON source_observations(term, number, purpose);
    CREATE INDEX ix_source_observations_selection ON source_observations(selection_id);
    """,
    """
    CREATE TABLE artifacts (
        sha256 TEXT PRIMARY KEY NOT NULL,
        byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
        recorded_at TEXT NOT NULL
    );
    CREATE INDEX ix_artifacts_recorded_at ON artifacts(recorded_at);
    CREATE TABLE selection_artifacts (
        selection_id TEXT NOT NULL REFERENCES document_selections(selection_id),
        part_scope TEXT NOT NULL CHECK(part_scope IN ('parts', 'observed_parts')),
        part_index INTEGER NOT NULL CHECK(part_index >= 0),
        artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
        PRIMARY KEY(selection_id, part_scope, part_index)
    );
    CREATE INDEX ix_selection_artifacts_artifact ON selection_artifacts(artifact_sha256);
    """,
    """
    CREATE TABLE artifact_reads (
        read_id TEXT PRIMARY KEY NOT NULL,
        requested_url TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
        byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
        recorded_at TEXT NOT NULL,
        UNIQUE(requested_url, artifact_sha256)
    );
    CREATE INDEX ix_artifact_reads_artifact ON artifact_reads(artifact_sha256);
    """,
    """
    CREATE TABLE content_snapshots (
        snapshot_id TEXT PRIMARY KEY NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('bill', 'change', 'plan')),
        payload_json TEXT NOT NULL
    );
    CREATE TABLE content_versions (
        content_id TEXT PRIMARY KEY NOT NULL,
        root_publication_id INTEGER NOT NULL,
        previous_version_id TEXT REFERENCES content_versions(content_id),
        bill_snapshot_id TEXT NOT NULL REFERENCES content_snapshots(snapshot_id),
        change_snapshot_id TEXT NOT NULL REFERENCES content_snapshots(snapshot_id),
        plan_snapshot_id TEXT NOT NULL REFERENCES content_snapshots(snapshot_id),
        version_json TEXT NOT NULL
    );
    CREATE INDEX ix_content_versions_root ON content_versions(root_publication_id);
    CREATE TABLE publication_content (
        publication_id INTEGER PRIMARY KEY NOT NULL,
        content_id TEXT NOT NULL REFERENCES content_versions(content_id)
    );
    CREATE INDEX ix_publication_content_version ON publication_content(content_id);
    """,
    """
    CREATE TABLE extraction_results (
        extraction_id TEXT PRIMARY KEY NOT NULL,
        artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
        recorded_at TEXT NOT NULL,
        result_json TEXT NOT NULL CHECK(length(CAST(result_json AS BLOB)) <= 16384)
    );
    CREATE INDEX ix_extraction_results_artifact ON extraction_results(artifact_sha256);
    CREATE TABLE selection_extractions (
        selection_id TEXT NOT NULL REFERENCES document_selections(selection_id),
        extraction_id TEXT NOT NULL REFERENCES extraction_results(extraction_id),
        PRIMARY KEY(selection_id, extraction_id)
    );
    """,
    """
    CREATE TABLE document_editions (
        edition_id TEXT PRIMARY KEY NOT NULL,
        recorded_at TEXT NOT NULL,
        edition_json TEXT NOT NULL CHECK(length(CAST(edition_json AS BLOB)) <= 1024)
    );
    CREATE TABLE selection_editions (
        selection_id TEXT PRIMARY KEY NOT NULL REFERENCES document_selections(selection_id),
        edition_id TEXT NOT NULL REFERENCES document_editions(edition_id)
    );
    CREATE INDEX ix_selection_editions_edition ON selection_editions(edition_id);
    """,
    """
    CREATE TABLE legacy_analysis_bodies (
        body_sha256 TEXT PRIMARY KEY NOT NULL,
        body_json TEXT NOT NULL
    );
    CREATE TABLE legacy_result_values (
        value_id TEXT PRIMARY KEY NOT NULL,
        envelope_json TEXT NOT NULL,
        analysis_body_sha256 TEXT REFERENCES legacy_analysis_bodies(body_sha256)
    );
    CREATE TABLE legacy_result_imports (
        import_id INTEGER PRIMARY KEY CHECK(import_id = 1),
        manifest_sha256 TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        source_sha256 TEXT
    );
    CREATE TABLE legacy_result_occurrences (
        value_id TEXT NOT NULL REFERENCES legacy_result_values(value_id),
        import_id INTEGER NOT NULL REFERENCES legacy_result_imports(import_id),
        source_table TEXT NOT NULL,
        source_key_json TEXT NOT NULL,
        source_column TEXT NOT NULL,
        source_path TEXT NOT NULL,
        container_term INTEGER,
        container_number TEXT,
        purpose TEXT,
        CHECK((container_term IS NULL) = (container_number IS NULL)),
        PRIMARY KEY(source_table, source_key_json, source_column, source_path)
    );
    CREATE INDEX ix_legacy_result_owner
      ON legacy_result_occurrences(container_term, container_number);
    """,
    """
    CREATE TABLE candidate_disposition_traces (
        trace_id TEXT PRIMARY KEY NOT NULL,
        coverage TEXT NOT NULL CHECK(coverage IN ('complete', 'omitted_over_limit')),
        candidate_count INTEGER NOT NULL CHECK(candidate_count >= 1),
        trace_json TEXT NOT NULL CHECK(length(CAST(trace_json AS BLOB)) <= 4096)
    );
    CREATE TABLE source_candidate_traces (
        observation_id TEXT NOT NULL REFERENCES source_observations(observation_id),
        trace_id TEXT NOT NULL REFERENCES candidate_disposition_traces(trace_id),
        PRIMARY KEY(observation_id, trace_id)
    );
    CREATE INDEX ix_source_candidate_traces_trace ON source_candidate_traces(trace_id);
    """,
    """
    CREATE TABLE rcl_letter_uses (
        use_id TEXT PRIMARY KEY NOT NULL,
        project_id INTEGER NOT NULL,
        stage_id INTEGER,
        stage_number INTEGER NOT NULL,
        letter_id INTEGER NOT NULL,
        payload_state TEXT NOT NULL CHECK(payload_state IN ('complete', 'omitted_over_limit')),
        artifact_sha256 TEXT REFERENCES artifacts(sha256),
        artifact_read_id TEXT REFERENCES artifact_reads(read_id),
        extraction_id TEXT REFERENCES extraction_results(extraction_id),
        semantic_sha256 TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        record_json TEXT NOT NULL CHECK(length(CAST(record_json AS BLOB)) <=
          CASE payload_state WHEN 'omitted_over_limit' THEN 2048 ELSE 4096 END)
    );
    CREATE INDEX ix_rcl_letter_uses_owner ON rcl_letter_uses(project_id, stage_id, letter_id);
    """,
    """
    CREATE TABLE senate_inspections (
        inspection_id TEXT PRIMARY KEY NOT NULL,
        semantic_sha256 TEXT NOT NULL,
        owner_term INTEGER NOT NULL,
        owner_number TEXT NOT NULL,
        resolution_term INTEGER NOT NULL,
        resolution_number TEXT NOT NULL,
        coverage TEXT NOT NULL CHECK(coverage IN ('complete', 'omitted_over_limit')),
        descriptor_count INTEGER NOT NULL CHECK(descriptor_count >= 0),
        link_count INTEGER NOT NULL CHECK(link_count >= 0 AND link_count <= descriptor_count),
        links_sha256 TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        record_json TEXT NOT NULL CHECK(length(CAST(record_json AS BLOB)) <=
          CASE coverage WHEN 'omitted_over_limit' THEN 2048 ELSE 8192 END),
        FOREIGN KEY(owner_term, owner_number) REFERENCES bills(term, number)
    );
    CREATE INDEX ix_senate_inspections_owner
      ON senate_inspections(owner_term, owner_number, resolution_number);
    CREATE TABLE senate_inspection_sources (
        inspection_id TEXT NOT NULL REFERENCES senate_inspections(inspection_id),
        descriptor_index INTEGER NOT NULL CHECK(descriptor_index >= 0),
        descriptor_sha256 TEXT NOT NULL,
        requested_url_sha256 TEXT NOT NULL,
        observation_id TEXT REFERENCES source_observations(observation_id),
        selection_id TEXT REFERENCES document_selections(selection_id),
        artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
        artifact_bytes INTEGER NOT NULL CHECK(artifact_bytes >= 0),
        reader_text_sha256 TEXT,
        artifact_read_id TEXT REFERENCES artifact_reads(read_id),
        extraction_id TEXT REFERENCES extraction_results(extraction_id),
        PRIMARY KEY(inspection_id, descriptor_index)
    );
    """,
    PHYSICAL_INVENTORY_MIGRATION,
    SOURCE_OMISSION_MIGRATION,
    SOURCE_REFERENCE_OMISSION_MIGRATION,
    SOURCE_CATALOG_MIGRATION,
    """
    CREATE TEMP TABLE saved_content_bindings AS SELECT * FROM publication_content;
    DROP TABLE publication_content;
    CREATE TABLE content_versions_next (
        content_id TEXT PRIMARY KEY NOT NULL,
        root_publication_id INTEGER NOT NULL,
        previous_version_id TEXT REFERENCES content_versions_next(content_id),
        bill_snapshot_id TEXT NOT NULL REFERENCES content_snapshots(snapshot_id),
        change_snapshot_id TEXT REFERENCES content_snapshots(snapshot_id),
        plan_snapshot_id TEXT NOT NULL REFERENCES content_snapshots(snapshot_id),
        version_json TEXT NOT NULL
    );
    INSERT INTO content_versions_next(rowid,content_id,root_publication_id,previous_version_id,
        bill_snapshot_id,change_snapshot_id,plan_snapshot_id,version_json)
        SELECT rowid,* FROM content_versions ORDER BY rowid;
    DROP TABLE content_versions;
    ALTER TABLE content_versions_next RENAME TO content_versions;
    CREATE INDEX ix_content_versions_root ON content_versions(root_publication_id);
    CREATE TABLE publication_content (
        publication_id INTEGER PRIMARY KEY NOT NULL,
        content_id TEXT NOT NULL REFERENCES content_versions(content_id)
    );
    INSERT INTO publication_content SELECT * FROM saved_content_bindings;
    DROP TABLE saved_content_bindings;
    CREATE INDEX ix_publication_content_version ON publication_content(content_id);
    """,
    """
    CREATE TABLE amendment_input_snapshots (
        snapshot_id TEXT PRIMARY KEY NOT NULL,
        profile TEXT NOT NULL CHECK(profile='amendments-text-question-v1'),
        question_sha256 TEXT NOT NULL,
        question_json TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE amendment_input_texts (
        text_sha256 TEXT PRIMARY KEY NOT NULL,
        text TEXT NOT NULL
    );
    ALTER TABLE amendment_input_snapshots ADD COLUMN text_sha256 TEXT
        REFERENCES amendment_input_texts(text_sha256);
    """,
    """
    ALTER TABLE publications ADD COLUMN telegram_chat_id INTEGER
        CHECK(telegram_chat_id IS NULL OR (
            typeof(telegram_chat_id) = 'integer'
            AND telegram_chat_id BETWEEN -4503599627370495 AND -1
            AND kind = 'status_update' AND status = 'sent'
            AND typeof(message_id) = 'integer' AND message_id > 0
            AND sent_at IS NOT NULL AND sent_at != ''
        ));
    """,
    """
    CREATE TEMP TABLE saved_content_bindings AS SELECT * FROM publication_content;
    DROP TABLE publication_content;
    CREATE TABLE content_versions_next (
        content_id TEXT PRIMARY KEY NOT NULL,
        root_publication_id INTEGER NOT NULL,
        previous_version_id TEXT REFERENCES content_versions_next(content_id),
        bill_snapshot_id TEXT REFERENCES content_snapshots(snapshot_id),
        change_snapshot_id TEXT REFERENCES content_snapshots(snapshot_id),
        plan_snapshot_id TEXT NOT NULL REFERENCES content_snapshots(snapshot_id),
        version_json TEXT NOT NULL
    );
    INSERT INTO content_versions_next(rowid,content_id,root_publication_id,previous_version_id,
        bill_snapshot_id,change_snapshot_id,plan_snapshot_id,version_json)
        SELECT rowid,* FROM content_versions ORDER BY rowid;
    DROP TABLE content_versions;
    ALTER TABLE content_versions_next RENAME TO content_versions;
    CREATE INDEX ix_content_versions_root ON content_versions(root_publication_id);
    CREATE TABLE publication_content (
        publication_id INTEGER PRIMARY KEY NOT NULL,
        content_id TEXT NOT NULL REFERENCES content_versions(content_id)
    );
    INSERT INTO publication_content SELECT * FROM saved_content_bindings;
    DROP TABLE saved_content_bindings;
    CREATE INDEX ix_publication_content_version ON publication_content(content_id);
    """,
    """
    CREATE TABLE retired_stage_anchors (
        id INTEGER PRIMARY KEY CHECK (id > 0),
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        message_id INTEGER NOT NULL CHECK (message_id > 0),
        sent_at TEXT NOT NULL
    ) STRICT;
    """,
    """
    CREATE TABLE retired_card_roots (
        id INTEGER PRIMARY KEY CHECK (id > 0),
        content_id TEXT NOT NULL REFERENCES content_versions(content_id),
        term INTEGER NOT NULL,
        number TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('new_bill', 'joint_bill')),
        ref TEXT,
        created_at TEXT NOT NULL,
        message_id INTEGER NOT NULL CHECK (message_id > 0),
        sent_at TEXT NOT NULL
    ) STRICT;
    """,
    """
    CREATE TABLE government_owner_moves (
        number TEXT NOT NULL CHECK (number GLOB 'RCL/?*' OR number GLOB 'WPL/?*'),
        from_term INTEGER NOT NULL CHECK (from_term > 0),
        to_term INTEGER NOT NULL CHECK (to_term > from_term),
        PRIMARY KEY (number, from_term),
        UNIQUE (number, to_term)
    ) STRICT;
    """,
    """
    CREATE TABLE primary_claim_technical_receipts (
        receipt_id TEXT PRIMARY KEY NOT NULL,
        result_id TEXT NOT NULL REFERENCES computation_results(result_id),
        receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)
            AND length(CAST(receipt_json AS BLOB)) <= 8192)
    ) STRICT;
    CREATE INDEX ix_primary_claim_technical_receipts_result
        ON primary_claim_technical_receipts(result_id, receipt_id);
    """,
)

SCHEMA_VERSION = len(MIGRATIONS)
_VERSION_LINE = "PRAGMA user_version = "


def _batch_log_fields(
    term: int,
    number: str,
    custom_id: str,
    meta: BatchItemMeta | DigestItemMeta,
    *,
    batch_id: str | None = None,
    request_sha256: str | None = None,
) -> LogFields:
    fields: LogFields = {
        "bill_term": term,
        "bill_number": number,
        "custom_id": custom_id,
        "batch_id": batch_id,
        "request_sha256": request_sha256 if request_sha256 is not None else meta.request_sha256,
        "selection_id": None,
        "artifact_sha256": None,
        "extraction_id": None,
    }
    receipt = meta.prepared_input_receipt
    if isinstance(receipt, (PrimaryInputReceipt, SupplementInputReceipt, AmendmentInputReceipt)):
        fields["selection_id"] = receipt.source_selection_id
        fields["artifact_sha256"] = receipt.artifact_sha256
        fields["extraction_id"] = receipt.extraction_id
    return fields


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_llm_batch(row: sqlite3.Row) -> LlmBatch:
    return LlmBatch(
        batch_id=row["batch_id"],
        provider=row["provider"],
        call_kind=row["call_kind"],
        submitted_at=_parse_dt(row["submitted_at"]) or datetime.min,
        status=row["status"],
        polled_at=_parse_dt(row["polled_at"]),
        completed_at=_parse_dt(row["completed_at"]),
        request_count=row["request_count"],
        estimated_cost_usd=row["estimated_cost_usd"],
        forgotten_at=_parse_dt(row["forgotten_at"]),
    )


def _row_to_llm_batch_item(row: sqlite3.Row, connection: sqlite3.Connection) -> LlmBatchItem:
    item = LlmBatchItem(
        batch_id=row["batch_id"],
        custom_id=row["custom_id"],
        call_kind=row["call_kind"],
        term=row["term"],
        number=row["number"],
        meta=read_legacy_batch_item_meta(connection, row),
        consumed_at=_parse_dt(row["consumed_at"]),
        result=BatchResult.model_validate_json(row["result_json"]) if row["result_json"] else None,
    )

    validate_item_input(connection, item)
    return item


def _utc_iso(value: datetime) -> str:
    """A range bound spelled as the column holds it: `sent_at` comes from the clock, which is
    UTC-aware, so a naive bound would sort beside the rows instead of among them."""
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _dump_version(script: str) -> int:
    for line in reversed(script.splitlines()):
        if line.startswith(_VERSION_LINE):
            return int(line[len(_VERSION_LINE) :].rstrip(";").strip())
    return 1


log = logging.getLogger(__name__)
_GROUPED_EDIT_REJECTED = "Telegram rejected the verified edit"


class RclLetterUseRowError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def verify_rcl_letter_use_parents(connection: sqlite3.Connection, use: RclLetterUse) -> None:
    physical = use.payload.physical
    artifact = None
    if physical.artifact_sha256 is not None:
        row = connection.execute(
            "SELECT * FROM artifacts WHERE sha256=?", (physical.artifact_sha256,)
        ).fetchone()
        if row is not None:
            artifact = ArtifactMetadata(
                sha256=row["sha256"],
                byte_size=row["byte_size"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
            )
    read = None
    if physical.artifact_read_id is not None:
        row = connection.execute(
            "SELECT * FROM artifact_reads WHERE read_id=?", (physical.artifact_read_id,)
        ).fetchone()
        if row is not None:
            read = ArtifactReadReceipt(
                sha256=row["artifact_sha256"],
                byte_size=row["byte_size"],
                requested_url=row["requested_url"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
            )
            if read.read_id != row["read_id"]:
                raise ValueError("letter read parent identity mismatch")
    extraction = None
    if physical.extraction_id is not None:
        row = connection.execute(
            "SELECT * FROM extraction_results WHERE extraction_id=?", (physical.extraction_id,)
        ).fetchone()
        if row is not None:
            try:
                extraction = read_extraction_result(row)
            except ExtractionResultRowError as exc:
                raise ValueError("letter extraction parent identity mismatch") from exc
    use.verify_parents(artifact, read, extraction)


def read_rcl_letter_use(connection: sqlite3.Connection, row: sqlite3.Row) -> RclLetterUseRecord:
    try:
        record = RclLetterUseRecord.model_validate_json(row["record_json"])
    except (ValueError, TypeError, IndexError) as exc:
        raise RclLetterUseRowError("invalid_rcl_letter_use") from exc
    official, physical = record.payload.official, record.payload.physical
    expected = (
        record.use_id,
        official.project_id,
        official.stage_id,
        official.stage_number,
        official.letter_id,
        record.payload_state,
        physical.artifact_sha256,
        physical.artifact_read_id,
        physical.extraction_id,
        record.semantic_sha256,
        _utc_iso(record.recorded_at),
    )
    columns = (
        "use_id",
        "project_id",
        "stage_id",
        "stage_number",
        "letter_id",
        "payload_state",
        "artifact_sha256",
        "artifact_read_id",
        "extraction_id",
        "semantic_sha256",
        "recorded_at",
    )
    if expected != tuple(row[name] for name in columns):
        raise RclLetterUseRowError("rcl_letter_use_columns_mismatch")
    try:
        verify_rcl_letter_use_parents(connection, record)
    except (ValueError, TypeError, sqlite3.Error) as exc:
        raise RclLetterUseRowError("rcl_letter_use_parent_mismatch") from exc
    return record


def _validate_rcl_letter_uses(connection: sqlite3.Connection) -> None:
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='rcl_letter_uses' AND type='table'"
    ).fetchone()
    if present is None:
        if connection.execute("PRAGMA user_version").fetchone()[0] >= 60:
            raise ValueError("letter-use table is missing from its schema")
        return
    factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        for row in connection.execute("SELECT * FROM rcl_letter_uses"):
            read_rcl_letter_use(connection, row)
    finally:
        connection.row_factory = factory


class SqliteBillRepository:
    """`BillRepository` on one sqlite3 connection; JSON columns hold pydantic dumps."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)  # autocommit, see begin()
        self._conn.row_factory = self._legacy_row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._in_txn = False

    def _legacy_row(self, cursor: sqlite3.Cursor, row: tuple[object, ...]) -> sqlite3.Row:
        columns = cursor.description
        if columns is None:
            return sqlite3.Row(cursor, row)
        values = {column[0]: value for column, value in zip(columns, row, strict=True)}
        term, number = values.get("term"), values.get("number")
        owner = (
            ProjectionOwner(term, number)
            if isinstance(term, int) and isinstance(number, str)
            else None
        )
        reader = PrimaryProjectionReader(self._conn)
        generation = values.get("analysis_generation")
        materialized = tuple(
            reader.materialize(
                column[0],
                read_legacy_json(value, self.get_legacy_result),
                owner,
                generation if isinstance(generation, int) else None,
            )
            if column[0] in LEGACY_PROJECTION_COLUMNS and isinstance(value, str)
            else value
            for column, value in zip(columns, row, strict=True)
        )
        return sqlite3.Row(cursor, materialized)

    def _preserve_legacy_json(self, raw: str, query: str, parameters: tuple[object, ...]) -> str:
        cursor = self._conn.cursor()
        cursor.row_factory = None
        try:
            row = cursor.execute(query, parameters).fetchone()
        finally:
            cursor.close()
        previous = row[0] if row is not None else None
        return preserve_legacy_json(
            raw, previous if isinstance(previous, str) else None, self.get_legacy_result
        )

    @property
    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> None:
        """Apply the migrations the database has not seen yet, each in its own transaction."""
        current = self.schema_version
        for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
            self._conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def atomic(self) -> Iterator[None]:
        self._conn.execute("SAVEPOINT process_plan")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK TO process_plan")
            raise
        finally:
            self._conn.execute("RELEASE process_plan")

    def begin(self) -> None:
        """Open the transaction a dry run rolls back at the end."""
        if not self._in_txn:
            self._conn.execute("BEGIN")
            self._in_txn = True

    def rollback(self) -> None:
        if not self._in_txn:
            return
        try:
            self._conn.execute("ROLLBACK")
        except sqlite3.OperationalError as exc:
            # Something underneath committed (`executescript` does). Nothing is left to undo,
            # and believing otherwise would make the next `begin()` a no-op: a dry run that writes.
            log.warning("nothing to roll back: %s", exc)
        finally:
            self._in_txn = False

    def dump(self) -> str:
        """The whole database as SQL text, ending with the schema version `restore()` reads."""
        return "\n".join(self._conn.iterdump()) + f"\n{_VERSION_LINE}{self.schema_version};\n"

    def restore(self, script: str) -> None:
        """Replace the current contents with a script produced by `dump()`.

        `executescript` commits as it goes, so the replacement cannot be one transaction: the
        script is replayed into a scratch database first, and the real one is only emptied once
        that has worked. Otherwise a truncated dump leaves no tables at all.
        """
        scratch = sqlite3.connect(":memory:")
        try:
            scratch.execute("PRAGMA foreign_keys = OFF")
            scratch.executescript(script)
            # A dump cut at a statement boundary replays cleanly and fails only in the migrations.
            version = _dump_version(script)
            validate_input_snapshots(scratch, include_results=False)
            validate_source_omissions(scratch)
            validate_catalog_attempts(scratch)
            for number, migration in enumerate(MIGRATIONS[version:], start=version + 1):
                scratch.executescript(f"BEGIN;{migration}PRAGMA user_version = {number};COMMIT;")
            validate_extraction_results(scratch)
            validate_review_memos(scratch)
            _validate_legacy_projections(scratch)
            validate_primary_projections(scratch)
            validate_status_supplement_dependencies(scratch)
            validate_secondary_dependencies(scratch)
            validate_document_work(scratch)
            validate_candidate_disposition_traces(scratch)
            _validate_rcl_letter_uses(scratch)
            validate_senate_inspections(scratch)
            validate_physical_inventory(scratch)
            DocumentSourceRows(scratch).validate_editions()
            validate_source_omissions(scratch)
            validate_catalog_attempts(scratch)
            validate_input_snapshots(scratch)
            validate_primary_claim_receipts(scratch)
            validate_projected_applications(scratch)
            validate_content_sources(scratch)
            validate_review_source_observations(scratch)
            validate_content_history(scratch)
            validate_amendment_inputs(scratch)
        finally:
            scratch.close()
        tables = [
            r[0]
            for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        # The dump creates tables in arbitrary order while publications reference status_changes,
        # so foreign-key enforcement must be off while the script runs.
        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            views = [
                row[0]
                for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='view'")
            ]
            for name in views:
                self._conn.execute('DROP VIEW IF EXISTS "' + name.replace('"', '""') + '"')
            for name in tables:
                self._conn.execute(f'DROP TABLE IF EXISTS "{name}"')
            self._conn.executescript(script)
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")
        # iterdump does not carry user_version, so dump() appends it; a dump written by an older
        # release lacks the line and is at most v1. migrate() then applies what is missing.
        self._conn.execute(f"PRAGMA user_version = {_dump_version(script)}")
        self.migrate()

        with self.atomic():
            for column, encode in _COMPACT_SNAPSHOTS.items():
                rows = self._conn.execute(
                    f"SELECT term, number, {column} FROM bills WHERE {column} IS NOT NULL"
                ).fetchall()
                for row in rows:
                    compact = encode(row[column])
                    if compact != row[column]:
                        self._conn.execute(
                            f"UPDATE bills SET {column} = ? WHERE term = ? AND number = ?",
                            (compact, row["term"], row["number"]),
                        )

    def bill_exists(self, term: int, number: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM bills WHERE term=? AND number=?", (term, number)
            ).fetchone()
            is not None
        )

    def get(self, term: int, number: str) -> Bill | None:
        row = self._conn.execute(
            "SELECT * FROM bills WHERE term = ? AND number = ?", (term, number)
        ).fetchone()
        return self._row_to_bill(row) if row else None

    def known_terms(self) -> list[int]:
        rows = self._conn.execute("SELECT DISTINCT term FROM bills ORDER BY term").fetchall()
        return [int(r[0]) for r in rows]

    def upsert_summary(self, summary: ProcessSummary, *, now: datetime) -> Bill:
        existing = self.get(summary.term, summary.number)
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO bills (term, number, title, change_date, closure_date, passed, status,
                                   summary_json, first_seen_at, last_checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.term,
                    summary.number,
                    summary.title,
                    summary.change_date.isoformat(),
                    _iso_date(summary.closure_date),
                    _bool(summary.passed),
                    BillStatus.DISCOVERED.value,
                    storage_json(summary),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        else:
            # rclNum and rclLink are in a process's detail only: a listing row must not erase them.
            summary = summary.model_copy(
                update={
                    "rcl_num": summary.rcl_num or existing.summary.rcl_num,
                    "rcl_link": summary.rcl_link or existing.summary.rcl_link,
                }
            )
            self._conn.execute(
                """
                UPDATE bills SET title = ?, change_date = ?, closure_date = ?, passed = ?,
                                 summary_json = ?, last_checked_at = ?
                WHERE term = ? AND number = ?
                """,
                (
                    summary.title,
                    summary.change_date.isoformat(),
                    _iso_date(summary.closure_date),
                    _bool(summary.passed),
                    storage_json(summary),
                    now.isoformat(),
                    summary.term,
                    summary.number,
                ),
            )
        bill = self.get(summary.term, summary.number)
        assert bill is not None, "the row was written just above on this connection"
        return bill

    _SKIPPED = frozenset(
        {
            BillStatus.SKIPPED_PREFILTER,
            BillStatus.SKIPPED_TEXT_PREFILTER,
            BillStatus.SKIPPED_COST,
            BillStatus.SKIPPED_CLOSED,
        }
    )

    def set_status(
        self,
        term: int,
        number: str,
        status: BillStatus,
        *,
        prefilter_hits: list[str] | None = None,
        reason: str | None = None,
        clear_reason: bool = False,
    ) -> None:
        hits = None
        if prefilter_hits is not None:
            hits = json.dumps(prefilter_hits, ensure_ascii=False)
        self._conn.execute(
            """
            UPDATE bills SET status = ?,
                prefilter_hits = COALESCE(?, prefilter_hits),
                last_error = CASE WHEN ? THEN NULL ELSE COALESCE(?, last_error) END
            WHERE term = ? AND number = ?
            """,
            (status.value, hits, clear_reason, reason, term, number),
        )
        if status in self._SKIPPED:
            self._drop_rcl_documents(term, number)

    def _drop_rcl_documents(self, term: int, number: str) -> None:
        """A skipped RCL project keeps its skeleton only: the documents are most of the row and
        are never read again (`reset` re-reads the project before an analysis)."""
        row = self._conn.execute(
            "SELECT rcl_json FROM bills WHERE term = ? AND number = ? AND rcl_json IS NOT NULL",
            (term, number),
        ).fetchone()
        if row is not None:
            project = RclProject.model_validate_json(row[0])
            self.save_rcl(term, number, project.without_documents())

    def save_stages(
        self, term: int, number: str, stages: tuple[Stage, ...], fingerprint: str
    ) -> None:
        self._conn.execute(
            "UPDATE bills SET stages_json = ?, stages_fingerprint = ?\n"
            "WHERE term = ? AND number = ?",
            (
                storage_json(stages),
                fingerprint,
                term,
                number,
            ),
        )

    def save_observed_closure(self, term: int, number: str, closed: date | None) -> None:
        self._conn.execute(
            "UPDATE bills SET observed_closure_date = ? WHERE term = ? AND number = ?",
            (closed.isoformat() if closed is not None else None, term, number),
        )

    def save_analysis(self, term: int, number: str, record: AnalysisRecord) -> None:
        """Store `record`; an equal model, not equal bytes, keeps the status and the generation."""
        row = self._conn.execute(
            "SELECT analysis_json, status, term, number FROM bills WHERE term = ? AND number = ?",
            (term, number),
        ).fetchone()
        if row is None:
            return
        stored = row["analysis_json"]
        same = stored is not None and AnalysisRecord.model_validate_json(stored) == record
        keeps = same and row["status"] in (
            BillStatus.BATCH_PENDING.value,
            BillStatus.REANALYSIS_READY.value,
        )
        encoded = self._preserve_legacy_json(
            record.model_dump_json(),
            "SELECT analysis_json FROM bills WHERE term = ? AND number = ?",
            (term, number),
        )
        encoded = PrimaryProjectionReader(self._conn).compact(
            encoded, ProjectionOwner(term, number)
        )
        self._conn.execute(
            """
            UPDATE bills SET analysis_json = ?,
                analysis_generation = analysis_generation + ?,
                ready_analysis_json = CASE WHEN ? THEN ready_analysis_json ELSE NULL END,
                status = ?,
                last_error = NULL
            WHERE term = ? AND number = ?
            """,
            (
                encoded,
                0 if same else 1,
                same,
                row["status"] if keeps else BillStatus.ANALYZED.value,
                term,
                number,
            ),
        )

        bill = self.get(term, number)
        assert bill is not None
        answer = record.analysis
        name = next(
            (
                value
                for value in (answer.mnemonic_candidate, answer.mnemonic_fallback)
                if valid_mnemonic(value)
            ),
            "",
        )
        if answer.relevant and name and answer.mnemonic_basis.strip():
            candidate = Mnemonic(
                name=name,
                language=answer.mnemonic_language,
                basis=answer.mnemonic_basis,
                selected_at=record.created_at,
                provisional=record.text_source == "metadata_only",
            )
            editorial = bill.editorial.select(candidate)
            if editorial.mnemonic is candidate and record.computation_id is not None:
                editorial = editorial.model_copy(
                    update={
                        "mnemonic": bind_mnemonic(
                            candidate, record, self.get_computation_result(record.computation_id)
                        )
                    }
                )
            self.save_editorial(term, number, editorial)

    def save_editorial(self, term: int, number: str, editorial: Editorial) -> None:
        self._conn.execute(
            "UPDATE bills SET editorial_json = ? WHERE term = ? AND number = ?",
            (editorial.model_dump_json(), term, number),
        )

    def save_ready_analysis(self, term: int, number: str, ready: ReadyAnalysis) -> None:
        self._conn.execute(
            "UPDATE bills SET ready_analysis_json = ? WHERE term = ? AND number = ?",
            (
                self._preserve_legacy_json(
                    ready.model_dump_json(),
                    "SELECT ready_analysis_json FROM bills WHERE term = ? AND number = ?",
                    (term, number),
                ),
                term,
                number,
            ),
        )

    def save_observed_process(self, term: int, number: str, observed: ObservedProcess) -> None:
        self._conn.execute(
            "UPDATE bills SET observed_process_json = ? WHERE term = ? AND number = ?",
            (storage_json(observed), term, number),
        )

    def load_analysis_memo(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in self._conn.execute("SELECT key, record_json, term, number FROM analysis_memo"):
            validate_review_memo_row(self._conn, row["record_json"], row["term"], row["number"])
            result[row["key"]] = row["record_json"]
        return result

    def get_legacy_result(self, value_id: str) -> LegacyResultValue | None:
        return _read_legacy_result(self._conn, value_id)

    def _legacy_snapshot_columns(self) -> tuple[LegacySnapshotColumn, ...]:
        result: list[LegacySnapshotColumn] = []
        cursor = self._conn.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            for table, shape in CONTEXT_RESULT_TABLES.items():
                columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
                owners = ("term", "number") if {"term", "number"} <= columns else ()
                selected = tuple(dict.fromkeys((*shape.keys, *shape.columns, *owners)))
                for row in cursor.execute(
                    f"SELECT {', '.join(selected)} FROM {table} ORDER BY {', '.join(shape.keys)}"
                ):
                    key = [row[part] for part in shape.keys]
                    if any(
                        not isinstance(part, (str, int)) or isinstance(part, bool) for part in key
                    ):
                        raise ValueError("a legacy snapshot requires exact string/integer keys")
                    term = row["term"] if owners else None
                    number = row["number"] if owners else None
                    known_owner = isinstance(term, int) and isinstance(number, str)
                    for column in shape.columns:
                        raw = row[column]
                        if raw is None:
                            continue
                        if not isinstance(raw, str):
                            raise ValueError("a saved legacy column requires JSON text")
                        result.append(
                            LegacySnapshotColumn(
                                table,
                                canonical_legacy_json(key),
                                column,
                                raw,
                                term if known_owner else None,
                                number if known_owner else None,
                            )
                        )
        finally:
            cursor.close()
        return tuple(result)

    def _legacy_maintenance_views(self) -> tuple[str, str]:
        def digest(query: str, decode: Callable[[sqlite3.Row], Bill | StatusChange]) -> str:
            fingerprint = hashlib.sha256()
            for row in self._conn.execute(query):
                raw = decode(row).model_dump_json().encode()
                fingerprint.update(len(raw).to_bytes(8, "big"))
                fingerprint.update(raw)
            return fingerprint.hexdigest()

        return (
            digest("SELECT * FROM bills ORDER BY term, number", self._row_to_bill),
            digest("SELECT * FROM status_changes ORDER BY id", self._row_to_status_change),
        )

    def _legacy_settled_batch_inputs(self) -> dict[str, tuple[str, str]]:
        return {
            canonical_legacy_json([batch_id, custom_id]): (purpose, provider)
            for batch_id, custom_id, purpose, provider in self._conn.execute(
                batch_retention_cte() + " SELECT i.batch_id,i.custom_id,i.call_kind,b.provider"
                " FROM llm_batch_items i JOIN llm_batches b USING (batch_id)"
                " WHERE i.call_kind IN ('analysis','reanalysis')"
                " AND json_type(i.meta_json,'$.text')='text' AND " + batch_payload_cleanup_filter()
            )
        }

    def plan_legacy_maintenance(
        self,
        *,
        imported_at: datetime,
        share_analysis_bodies: bool = True,
        retain_batch_text: bool = False,
    ) -> LegacyMaintenancePlan:
        with self.atomic():
            _validate_legacy_import(self._conn)
            snapshot_sha = hashlib.sha256(self.dump().encode()).hexdigest()
            marker = self._conn.execute(
                "SELECT manifest_sha256 FROM legacy_result_imports WHERE import_id = 1"
            ).fetchone()
            if marker is not None:
                _validate_legacy_projections(self._conn)
                return LegacyMaintenancePlan(
                    snapshot_sha,
                    imported_at,
                    share_analysis_bodies,
                    existing_manifest_sha256=marker[0],
                    retain_batch_text=retain_batch_text,
                )
            return plan_legacy_maintenance(
                self._legacy_snapshot_columns(),
                snapshot_sha256=snapshot_sha,
                imported_at=imported_at,
                share_analysis_bodies=share_analysis_bodies,
                retain_batch_text=retain_batch_text,
                settled_batch_inputs=self._legacy_settled_batch_inputs()
                if retain_batch_text
                else None,
            )

    def apply_legacy_maintenance(self, plan: LegacyMaintenancePlan) -> LegacyMaintenanceResult:
        with self.atomic():
            _validate_legacy_import(self._conn)
            marker = self._conn.execute(
                "SELECT manifest_sha256 FROM legacy_result_imports WHERE import_id = 1"
            ).fetchone()
            if marker is not None:
                _validate_legacy_projections(self._conn)
                expected_manifest = (
                    LegacyResultImport.model_validate_json(
                        plan.imported.model_dump_json()
                    ).manifest_sha256
                    if plan.imported is not None
                    else plan.existing_manifest_sha256
                )
                if expected_manifest != marker[0]:
                    raise ValueError("the finite legacy import already has a different manifest")
                return LegacyMaintenanceResult(False, marker[0])
            baseline = self.dump().encode()
            snapshot_sha = hashlib.sha256(baseline).hexdigest()
            if snapshot_sha != plan.snapshot_sha256:
                raise ValueError("the legacy maintenance raw snapshot changed")
            columns = self._legacy_snapshot_columns()
            expected = plan_legacy_maintenance(
                columns,
                snapshot_sha256=snapshot_sha,
                imported_at=plan.imported_at,
                share_analysis_bodies=plan.share_analysis_bodies,
                retain_batch_text=plan.retain_batch_text,
                settled_batch_inputs=(
                    self._legacy_settled_batch_inputs() if plan.retain_batch_text else None
                ),
            )
            if plan != expected:
                raise ValueError("the legacy maintenance plan does not match its exact snapshot")
            if plan.imported is None:
                return LegacyMaintenanceResult(False, None)
            views = self._legacy_maintenance_views()
            self.import_legacy_results(plan.imported)
            for shared in plan.shared:
                self._conn.execute(
                    "INSERT OR IGNORE INTO legacy_analysis_bodies VALUES (?, ?)",
                    (shared.body_sha256, shared.body_json),
                )
                self._conn.execute(
                    "UPDATE legacy_result_values SET envelope_json = ?, analysis_body_sha256 = ?"
                    " WHERE value_id = ?",
                    (shared.envelope_json, shared.body_sha256, shared.value_id),
                )
            for replacement in plan.replacements:
                original = replacement.original
                shape = CONTEXT_RESULT_TABLES[original.table]
                key = legacy_json(original.key_json)
                assert isinstance(key, list), "the recomputed plan validates physical key arrays"
                predicate = " AND ".join(f"{part} = ?" for part in shape.keys)
                changed = self._conn.execute(
                    f"UPDATE {original.table} SET {original.column} = ? WHERE {predicate}"
                    f" AND {original.column} = ?",
                    (replacement.replacement_json, *key, original.raw_json),
                ).rowcount
                if changed != 1:
                    raise ValueError("a legacy projection physical value changed")
            _validate_legacy_projections(self._conn)
            if any(
                self.get_legacy_result(value.value_id) != value for value in plan.imported.values
            ):
                raise ValueError("legacy maintenance changed an immutable whole value")
            if self._conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("legacy maintenance requires valid foreign keys")
            batch_texts = {
                value.value_id: body.text
                for value in plan.imported.values
                if (body := value.batch_text) is not None
            }
            relocated = {
                occurrence.source_key_json: batch_texts[occurrence.value_id]
                for occurrence in plan.imported.occurrences
                if occurrence.value_id in batch_texts
            }
            for original, current in zip(columns, self._legacy_snapshot_columns(), strict=True):
                hydrated = read_legacy_json(current.raw_json, self.get_legacy_result)
                value = legacy_json(hydrated)
                if (
                    current.table == "llm_batch_items"
                    and (relocated_text := relocated.get(current.key_json)) is not None
                ):
                    if not isinstance(value, dict) or "text" in value:
                        raise ValueError("legacy maintenance must relocate exact batch text once")
                    value["text"] = relocated_text
                if canonical_legacy_json(value) != canonical_legacy_json(
                    legacy_json(original.raw_json)
                ):
                    raise ValueError("legacy maintenance changed a saved JSON view")
            if self._legacy_maintenance_views() != views:
                raise ValueError("legacy maintenance changed a typed reader view")
            compact = self.dump()
            restored = SqliteBillRepository(":memory:")
            try:
                restored.restore(compact)
                if restored.dump() != compact or restored._legacy_maintenance_views() != views:
                    raise ValueError("legacy maintenance failed exact dump/restore parity")
            finally:
                restored.close()
            baseline_gzip = len(gzip.compress(baseline, compresslevel=9, mtime=0))
            compact_bytes = compact.encode()
            compact_gzip = len(gzip.compress(compact_bytes, compresslevel=9, mtime=0))
            if compact_gzip * 100 > baseline_gzip * 105:
                raise ValueError("legacy maintenance exceeds the 105% gzip budget")
            return LegacyMaintenanceResult(
                True,
                plan.imported.manifest_sha256,
                len(baseline),
                baseline_gzip,
                len(compact_bytes),
                compact_gzip,
            )

    def import_legacy_results(self, imported: LegacyResultImport) -> bool:
        imported = LegacyResultImport.model_validate_json(imported.model_dump_json())
        with self.atomic():
            _validate_legacy_import(self._conn)
            previous = self._conn.execute(
                "SELECT manifest_sha256 FROM legacy_result_imports WHERE import_id = 1"
            ).fetchone()
            if previous is not None:
                if previous[0] != imported.manifest_sha256:
                    raise ValueError("the finite legacy import already has a different manifest")
                return False
            self._conn.execute(
                "INSERT INTO legacy_result_imports VALUES (1, ?, ?, ?)",
                (
                    imported.manifest_sha256,
                    imported.imported_at.isoformat(),
                    imported.source_sha256,
                ),
            )
            self._conn.executemany(
                "INSERT INTO legacy_result_values(value_id, envelope_json) VALUES (?, ?)",
                [(value.value_id, value.value_json) for value in imported.values],
            )
            self._conn.executemany(
                "INSERT INTO legacy_result_occurrences"
                " (value_id, import_id, source_table, source_key_json, source_column, source_path,"
                " container_term, container_number, purpose) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.value_id,
                        item.source_table,
                        item.source_key_json,
                        item.source_column,
                        item.source_path,
                        item.container_term,
                        item.container_number,
                        item.purpose,
                    )
                    for item in imported.occurrences
                ],
            )
            _validate_legacy_import(self._conn)
        return True

    def list_legacy_result_occurrences(
        self, term: int, number: str
    ) -> list[LegacyResultOccurrence]:
        rows = self._conn.execute(
            "SELECT value_id, source_table, source_key_json, source_column, source_path,"
            " container_term, container_number, purpose FROM legacy_result_occurrences"
            " WHERE container_term = ? AND container_number = ?"
            " ORDER BY source_table, source_key_json, source_column, source_path, value_id",
            (term, number),
        )
        return [LegacyResultOccurrence.model_validate(dict(row)) for row in rows]

    def save_computation_result(self, result: ComputationResult) -> None:
        validate_result_input(self._conn, result)
        DocumentSourceRows(self._conn).review_source_observations(result)
        self._conn.execute(
            "INSERT INTO computation_results"
            " (result_id, provider, batch_id, custom_id, term, number, result_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                result.result_id,
                result.provider,
                result.batch_id,
                result.custom_id,
                result.term,
                result.number,
                result.model_dump_json(),
            ),
        )
        if self.get_computation_result(result.result_id) != result:
            raise ValueError("an immutable computation result cannot be replaced")
        log.debug(
            "local computation result write",
            extra=log_extra(
                bill_term=result.term,
                bill_number=result.number,
                computation_id=result.result_id,
                batch_id=result.batch_id,
                custom_id=result.custom_id,
                request_sha256=result.request_sha256,
            ),
        )

    def list_primary_claim_technical_receipts(
        self, result_id: str
    ) -> tuple[CheckedPrimaryClaimReceipt | CheckedPrimaryComparisonReceipt, ...]:
        return read_primary_claim_receipts(self._conn, result_id)

    def get_computation_result(self, result_id: str) -> ComputationResult | None:
        return ContentSourceRows(self._conn).get_computation_result(result_id)

    def list_computation_results(
        self, term: int, number: str, *, purpose: BatchKind | None = None
    ) -> list[ComputationResult]:
        query = "SELECT * FROM computation_results WHERE term = ? AND number = ?"
        parameters: tuple[int | str, ...] = (term, number)
        if purpose is not None:
            query += " AND json_extract(result_json, '$.purpose') = ?"
            parameters += (purpose,)
        return [
            checked_computation_result(self._conn, row)
            for row in self._conn.execute(query + " ORDER BY rowid", parameters)
        ]

    def save_applicability_observation(self, observation: ResultApplicabilityObservation) -> None:
        validated = ResultApplicabilityObservation.model_validate_json(
            observation.model_dump_json()
        )
        if validated.observation_id != observation.observation_id:
            raise ValueError("an applicability observation must keep its validated identity")
        observation = validated
        self._conn.execute(
            "INSERT INTO result_applicability"
            " (observation_id, result_id, term, number, observation_json) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (
                observation.observation_id,
                observation.result_id,
                observation.term,
                observation.number,
                observation.model_dump_json(),
            ),
        )
        saved = self.get_applicability_observation(observation.observation_id)
        assert saved is not None, "the insert retains the observation under its immutable identity"
        if saved != observation.model_copy(update={"created_at": saved.created_at}):
            raise ValueError("an immutable applicability observation cannot be replaced")
        log.debug(
            "local applicability observation write",
            extra=log_extra(
                bill_term=observation.term,
                bill_number=observation.number,
                observation_id=observation.observation_id,
                computation_id=observation.result_id,
            ),
        )

    def get_applicability_observation(
        self, observation_id: str
    ) -> ResultApplicabilityObservation | None:
        row = self._conn.execute(
            "SELECT * FROM result_applicability WHERE observation_id=?", (observation_id,)
        ).fetchone()
        return self._applicability_observation(row) if row else None

    def list_applicability_observations(
        self, term: int, number: str
    ) -> list[ResultApplicabilityObservation]:
        return [
            self._applicability_observation(row)
            for row in self._conn.execute(
                "SELECT * FROM result_applicability WHERE term=? AND number=? ORDER BY rowid",
                (term, number),
            )
        ]

    @staticmethod
    def _applicability_observation(row: sqlite3.Row) -> ResultApplicabilityObservation:
        observation = ResultApplicabilityObservation.model_validate_json(row["observation_json"])
        expected = (
            observation.observation_id,
            observation.result_id,
            observation.term,
            observation.number,
        )
        stored = tuple(row[key] for key in ("observation_id", "result_id", "term", "number"))
        if expected != stored:
            raise ValueError("applicability observation columns differ from the recorded identity")
        return observation

    def save_source_observation(
        self, selection: DocumentSelectionRecord, observation: SourceObservation
    ) -> None:
        selection = DocumentSelectionRecord.model_validate_json(selection.model_dump_json())
        observation = SourceObservation.model_validate_json(observation.model_dump_json())
        if observation.selection_id != selection.selection_id:
            raise ValueError("a source observation must reference its exact selection metadata")
        with self.atomic():
            self._conn.execute(
                "INSERT INTO document_selections"
                " (selection_id, question, policy_version, state, record_json)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (
                    selection.selection_id,
                    selection.selection.question,
                    selection.selection.policy_version,
                    selection.selection.state,
                    selection.model_dump_json(),
                ),
            )
            if self.get_document_selection(selection.selection_id) != selection:
                raise ValueError("an immutable document selection cannot be replaced")
            self._conn.execute(
                "INSERT INTO source_observations"
                " (observation_id, term, number, purpose, selection_id, source_url, source_kind,"
                " observation_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (
                    observation.observation_id,
                    observation.term,
                    observation.number,
                    observation.purpose,
                    observation.selection_id,
                    observation.document.url,
                    observation.document.kind,
                    observation.model_dump_json(),
                ),
            )
            saved = self.get_source_observation(observation.observation_id)
            assert saved is not None, "the insert retains the observation under its identity"
            if saved != observation.model_copy(update={"recorded_at": saved.recorded_at}):
                raise ValueError("an immutable source observation cannot be replaced")
        log.debug(
            "local source observation write",
            extra=log_extra(
                bill_term=observation.term,
                bill_number=observation.number,
                observation_id=observation.observation_id,
                selection_id=selection.selection_id,
            ),
        )

    def save_selection_edition(self, selection_id: str, edition: DocumentEdition) -> None:
        edition = DocumentEdition.model_validate_json(edition.model_dump_json())
        with self.atomic():
            selection = self.get_document_selection(selection_id)
            if (
                selection is None
                or candidate_edition(selection, recorded_at=edition.recorded_at) != edition
            ):
                raise ValueError("an edition requires its selection's exact candidate membership")
            self._conn.execute(
                "INSERT INTO document_editions (edition_id, recorded_at, edition_json)"
                " VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (edition.edition_id, _utc_iso(edition.recorded_at), edition.model_dump_json()),
            )
            saved = self.get_document_edition(edition.edition_id)
            if saved is None or saved != edition.model_copy(
                update={"recorded_at": saved.recorded_at}
            ):
                raise ValueError("an immutable edition cannot be replaced")
            self._conn.execute(
                "INSERT INTO selection_editions (selection_id, edition_id)"
                " VALUES (?, ?) ON CONFLICT DO NOTHING",
                (selection_id, edition.edition_id),
            )
            reference = self.get_selection_edition(selection_id)
            if reference != SelectionEditionReference(
                selection_id=selection_id, edition_id=edition.edition_id
            ):
                raise ValueError("an immutable selection edition cannot be rebound")

    def get_document_edition(self, edition_id: str) -> DocumentEdition | None:
        return DocumentSourceRows(self._conn).get_document_edition(edition_id)

    def get_selection_edition(self, selection_id: str) -> SelectionEditionReference | None:
        return DocumentSourceRows(self._conn).get_selection_edition(selection_id)

    def get_document_selection(self, selection_id: str) -> DocumentSelectionRecord | None:
        return ContentSourceRows(self._conn).get_document_selection(selection_id)

    def get_source_observation(self, observation_id: str) -> SourceObservation | None:
        return ContentSourceRows(self._conn).get_source_observation(observation_id)

    def list_source_observations(self, term: int, number: str) -> list[SourceObservation]:
        return [
            self._source_observation(row)
            for row in self._conn.execute(
                "SELECT * FROM source_observations WHERE term=? AND number=? ORDER BY rowid",
                (term, number),
            )
        ]

    def source_observations_for_selection(
        self, term: int, number: str, selection_id: str
    ) -> list[SourceObservation]:
        return ContentSourceRows(self._conn).source_observations_for_selection(
            term, number, selection_id
        )

    def save_candidate_disposition_trace(
        self, observation_id: str, trace: CandidateDispositionTrace
    ) -> None:
        trace = CandidateDispositionTrace.model_validate_json(
            trace.model_dump_json(exclude_defaults=True)
        )
        with self.atomic():
            if self.get_source_observation(observation_id) is None:
                raise ValueError("a candidate trace requires a recorded source observation")
            validate_candidate_trace_link(self._conn, trace, observation_id)
            self._conn.execute(
                "INSERT INTO candidate_disposition_traces"
                " (trace_id, coverage, candidate_count, trace_json) VALUES (?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (
                    trace.trace_id,
                    trace.coverage,
                    trace.candidate_count,
                    trace.model_dump_json(exclude_defaults=True),
                ),
            )
            self._conn.execute(
                "INSERT INTO source_candidate_traces (observation_id, trace_id) VALUES (?, ?)"
                " ON CONFLICT DO NOTHING",
                (observation_id, trace.trace_id),
            )
            if self.get_candidate_disposition_trace(trace.trace_id) != trace:
                raise ValueError("an immutable candidate trace cannot be replaced")

    def get_candidate_disposition_trace(self, trace_id: str) -> CandidateDispositionTrace | None:
        row = self._conn.execute(
            "SELECT * FROM candidate_disposition_traces WHERE trace_id=?", (trace_id,)
        ).fetchone()
        if row is None:
            return None
        trace = read_candidate_disposition_trace(row)
        validate_candidate_trace_links(self._conn, trace)
        return trace

    def list_candidate_disposition_traces(
        self, observation_id: str
    ) -> list[CandidateDispositionTrace]:
        traces = [
            read_candidate_disposition_trace(row)
            for row in self._conn.execute(
                "SELECT t.* FROM source_candidate_traces AS link"
                " JOIN candidate_disposition_traces AS t ON t.trace_id=link.trace_id"
                " WHERE link.observation_id=? ORDER BY t.trace_id",
                (observation_id,),
            )
        ]
        for trace in traces:
            validate_candidate_trace_link(self._conn, trace, observation_id)
        return traces

    @staticmethod
    def _source_observation(row: sqlite3.Row) -> SourceObservation:
        observation = SourceObservation.model_validate_json(row["observation_json"])
        expected = (
            observation.observation_id,
            observation.term,
            observation.number,
            observation.purpose,
            observation.selection_id,
            observation.document.url,
            observation.document.kind,
        )
        columns = (
            "observation_id",
            "term",
            "number",
            "purpose",
            "selection_id",
            "source_url",
            "source_kind",
        )
        if expected != tuple(row[key] for key in columns):
            raise ValueError("source observation columns differ from the recorded identity")
        return observation

    def save_amendment_input(self, snapshot: AmendmentInputSnapshot) -> None:
        try:
            snapshot = AmendmentInputSnapshot.model_validate_json(snapshot.model_dump_json())
        except ValueError, TypeError, RecursionError:
            raise ValueError("invalid_amendment_input") from None
        with self.atomic():
            self._conn.execute(
                "INSERT INTO amendment_input_snapshots"
                "(snapshot_id,profile,question_sha256,question_json)"
                " VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                (
                    snapshot.snapshot_id,
                    snapshot.profile,
                    snapshot.question_sha256,
                    snapshot.question_json,
                ),
            )
            if self.get_amendment_input(snapshot.snapshot_id) != snapshot:
                raise ValueError("invalid_amendment_input")

    def get_amendment_input(self, snapshot_id: str) -> AmendmentInputSnapshot | None:
        return read_amendment_input(self._conn, snapshot_id)

    def get_amendment_scan_context(self, witness: AmendmentScanInputWitness) -> str:
        return read_amendment_scan_context(self._conn, witness)

    def save_content_snapshot(self, snapshot: ContentSnapshot) -> None:
        snapshot = ContentSnapshot.model_validate_json(snapshot.model_dump_json())
        self._conn.execute(
            "INSERT INTO content_snapshots (snapshot_id, kind, payload_json) VALUES (?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (snapshot.snapshot_id, snapshot.kind, snapshot.payload_json),
        )
        if self.get_content_snapshot(snapshot.snapshot_id) != snapshot:
            raise ValueError("an immutable content snapshot cannot be replaced")

    def get_content_snapshot(self, snapshot_id: str) -> ContentSnapshot | None:
        return ContentSourceRows(self._conn).get_content_snapshot(snapshot_id)

    def find_supplement_content_evidence(
        self, change: StatusChange, *, supplement_index: int | None = None
    ) -> tuple[int, SupplementContentEvidence] | None:
        return find_supplement_content_evidence(
            self._conn, change, supplement_index=supplement_index
        )

    def find_card_primary_evidence(self, bill: Bill) -> CardPrimaryEvidence | None:
        return card_primary_evidence_candidate(self._conn, bill)

    def save_multipart_card_revision(
        self, bill: ContentSnapshot, snapshot: ContentSnapshot, version: ContentVersion
    ) -> None:
        if (
            not self._conn.in_transaction
            or version.multipart_card_revision is None
            or version.bill_snapshot_id != bill.snapshot_id
            or version.plan_snapshot_id != snapshot.snapshot_id
        ):
            raise ValueError("multipart card history requires its atomic freeze")
        with self.atomic():
            self.save_content_snapshot(bill)
            self.save_content_snapshot(snapshot)
            edition = verify_multipart_card_revision(self._conn, version)
            if edition is None:
                raise ValueError("multipart card history requires physical slots")
            for identity in edition.bindings:
                publication = self.publication_by_id(identity)
                if (
                    publication is None
                    or publication.status is not PublicationStatus.QUEUED
                    or publication.attempts
                    or publication.message_id is not None
                    or self.get_publication_content(identity) is not None
                ):
                    raise ValueError("multipart card history requires untouched slots")
            self.save_content_version(version)
            self._conn.executemany(
                "INSERT INTO publication_content(publication_id,content_id) VALUES(?,?)",
                [(identity, version.content_id) for identity in edition.bindings],
            )
            verify_multipart_card_revision(self._conn, version, require_bound=True)

    def find_joint_comparison_application_id(
        self, bill: Bill, *, recorded_at: datetime
    ) -> int | None:
        try:
            return joint_comparison_application_id(self._conn, bill, recorded_at=recorded_at)
        except ValueError, TypeError, RecursionError, sqlite3.Error:
            return None

    def find_review_application_id(
        self,
        bill: Bill,
        record: DocumentReviewRecord,
        kind: PublicationKind,
        *,
        recorded_at: datetime,
    ) -> int | None:
        try:
            return direct_review_application_id(
                self._conn, bill, record, kind, recorded_at=recorded_at
            )
        except ValueError, TypeError, RecursionError, sqlite3.Error:
            return None

    def save_original_delivery_content(
        self,
        bill: ContentSnapshot,
        snapshot: ContentSnapshot,
        version: ContentVersion,
        *,
        primary: ContentSnapshot | None = None,
        change: ContentSnapshot | None = None,
    ) -> None:
        evidence = version.direct_stage_evidence
        direct = evidence is not None and evidence.multipart is not None
        original = version.original_delivery_evidence
        if (
            not self._conn.in_transaction
            or (original is None and not direct)
            or (direct and original is not None)
            or (change.snapshot_id if change else None) != version.change_snapshot_id
            or direct != (change is not None)
            or version.bill_snapshot_id != bill.snapshot_id
            or version.plan_snapshot_id != snapshot.snapshot_id
        ):
            raise ValueError("original delivery history requires its atomic freeze")
        joint = original.joint_primary if original is not None else None
        if (primary.snapshot_id if primary else None) != (
            joint.bill_snapshot_id if joint else None
        ):
            raise ValueError("original delivery history primary differs from its freeze")
        with self.atomic():
            self.save_content_snapshot(bill)
            self.save_content_snapshot(snapshot)
            if primary is not None:
                self.save_content_snapshot(primary)
            if change is not None:
                self.save_content_snapshot(change)
            verify = verify_direct_stage_content if direct else verify_original_delivery_content
            plan = verify(self._conn, version)
            if plan is None:
                raise ValueError("original delivery history requires physical parts")
            for identity in plan.bindings:
                publication = self.publication_by_id(identity)
                if (
                    publication is None
                    or publication.status is not PublicationStatus.QUEUED
                    or publication.attempts
                    or publication.message_id is not None
                    or self.get_publication_content(identity) is not None
                ):
                    raise ValueError("original delivery history requires untouched slots")
            self.save_content_version(version)
            self._conn.executemany(
                "INSERT INTO publication_content(publication_id,content_id) VALUES(?,?)",
                [(identity, version.content_id) for identity in plan.bindings],
            )
            verify(self._conn, version, require_bound=True)

    def save_digest_content(
        self,
        snapshot: ContentSnapshot,
        version: ContentVersion,
        *,
        part_snapshots: tuple[ContentSnapshot, ...] = (),
    ) -> None:
        if not self._conn.in_transaction or version.digest_profile is None:
            raise ValueError("digest capture requires its original atomic freeze")
        if snapshot.snapshot_id != version.plan_snapshot_id:
            raise ValueError("digest capture snapshot differs from its version")
        if len(part_snapshots) > 16 or bool(part_snapshots) != (
            version.digest_profile in RENDERED_DIGEST_PROFILES
        ):
            raise ValueError("digest capture part snapshots differ from its profile")
        with self.atomic():
            self.save_content_snapshot(snapshot)
            for part_snapshot in part_snapshots:
                self.save_content_snapshot(part_snapshot)
            plan = verify_digest_content(self._conn, version)
            if plan is None:
                raise ValueError("digest capture requires a known plan")
            if isinstance(plan, ResolvedDigestRenderedContent) and tuple(
                part.snapshot_id for part in plan.plan.parts
            ) != tuple(part.snapshot_id for part in part_snapshots):
                raise ValueError("digest capture requires its exact ordered part snapshots")
            for part in plan.parts:
                row = self.publication_by_id(part.publication_id)
                if (
                    row is None
                    or row.status is not PublicationStatus.QUEUED
                    or row.attempts
                    or row.message_id is not None
                    or self.get_publication_content(part.publication_id) is not None
                ):
                    raise ValueError("digest capture is restricted to untouched original parts")
            if self.list_content_versions(version.root_publication_id):
                raise ValueError("digest capture cannot backfill existing history")
            self.save_content_version(version)
            self._conn.executemany(
                "INSERT INTO publication_content(publication_id,content_id) VALUES(?,?)",
                [(part.publication_id, version.content_id) for part in plan.parts],
            )
            verify_digest_content(self._conn, version, require_bound=True)

    def save_content_version(self, version: ContentVersion) -> None:
        version = ContentVersion.model_validate_json(version.model_dump_json())
        verify_content_source(self._conn, version)
        verify_content_applications(self._conn, version, retained=False)
        verify_card_content(self._conn, version)
        verify_direct_stage_content(self._conn, version)
        verify_grouped_stage_content(self._conn, version)
        verify_digest_content(self._conn, version)
        verify_sitting_content(self._conn, version)
        verify_act_published_content(self._conn, version)
        verify_original_delivery_content(self._conn, version)
        verify_multipart_card_revision(self._conn, version)
        for kind, identity in content_snapshot_refs(version):
            snapshot = self.get_content_snapshot(identity)
            if snapshot is None or snapshot.kind != kind:
                raise ValueError("a content version requires matching semantic snapshots")
        if version.previous_version_id is not None:
            previous = self.get_content_version(version.previous_version_id)
            if previous is None or previous.root_publication_id != version.root_publication_id:
                raise ValueError("a content revision requires its own original history")
        self._conn.execute(
            "INSERT INTO content_versions (content_id, root_publication_id, previous_version_id,"
            " bill_snapshot_id, change_snapshot_id, plan_snapshot_id, version_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                version.content_id,
                version.root_publication_id,
                version.previous_version_id,
                version.bill_snapshot_id,
                version.change_snapshot_id,
                version.plan_snapshot_id,
                version.model_dump_json(),
            ),
        )
        saved = self.get_content_version(version.content_id)
        if saved != version.model_copy(
            update={"recorded_at": saved.recorded_at if saved else None}
        ):
            raise ValueError("an immutable content version cannot be replaced")
        log.debug(
            "local content version write",
            extra=log_extra(
                content_id=version.content_id, publication_id=version.root_publication_id
            ),
        )

    def get_content_version(self, content_id: str) -> ContentVersion | None:
        row = self._conn.execute(
            "SELECT * FROM content_versions WHERE content_id=?", (content_id,)
        ).fetchone()
        if row is None:
            return None
        version = read_content_version(row)
        self._verify_retained_content_version(version)
        return version

    def _verify_retained_content_version(
        self, version: ContentVersion, *, inventory: CardContentInventory | None = None
    ) -> int | None:
        verify_content_source(self._conn, version)
        verify_content_applications(self._conn, version)
        card_physical_id = verify_card_content(
            self._conn, version, retained=True, inventory=inventory
        )
        verify_direct_stage_content(self._conn, version)
        verify_grouped_stage_content(self._conn, version)
        verify_digest_content(self._conn, version)
        verify_sitting_content(self._conn, version)
        verify_act_published_content(self._conn, version)
        verify_original_delivery_content(self._conn, version)
        verify_multipart_card_revision(self._conn, version)
        return card_physical_id

    def list_content_versions(self, root_publication_id: int) -> list[ContentVersion]:
        versions: list[ContentVersion] = []
        for row in self._conn.execute(
            "SELECT * FROM content_versions WHERE root_publication_id=? ORDER BY rowid",
            (root_publication_id,),
        ):
            version = read_content_version(row)
            self._verify_retained_content_version(version)
            versions.append(version)
        return versions

    def read_card_content_history(self, root_publication_id: int) -> CardContentHistory:
        inventory = read_card_content_inventory(self._conn, root_publication_id)
        versions = []
        for row in self._conn.execute(
            "SELECT * FROM content_versions WHERE root_publication_id=? ORDER BY rowid",
            (root_publication_id,),
        ):
            version = read_content_version(row)
            self._verify_retained_content_version(version, inventory=inventory)
            versions.append(version)
        by_id = {version.content_id: version for version in versions}
        if not set(inventory.bindings.values()).issubset(by_id):
            raise ValueError("card history contains a foreign or missing binding")
        return CardContentHistory(
            versions,
            inventory.publications,
            {identity: by_id[content_id] for identity, content_id in inventory.bindings.items()},
        )

    def _validate_publication_content_binding(
        self, publication_id: int, content_id: str
    ) -> ContentVersion:
        version = self.get_content_version(content_id)
        publication = self.publication_by_id(publication_id)
        root = self.publication_by_id(version.root_publication_id) if version else None
        if version is None or publication is None or root is None:
            raise ValueError("a content binding requires its publication and original")
        if (publication.term, publication.number, publication.channel_id) != (
            root.term,
            root.number,
            root.channel_id,
        ):
            raise ValueError("content must belong to the same publication owner")
        verify_content_applications(
            self._conn, version, publication_id=publication_id, retained=False
        )
        if (
            version.digest_profile is not None
            or version.sitting_profile is not None
            or version.original_delivery_evidence is not None
            or (
                version.direct_stage_evidence is not None
                and version.direct_stage_evidence.multipart is not None
            )
            or version.multipart_card_revision is not None
        ):
            raise ValueError("history requires atomic complete physical binding")
        plan = publication.delivery
        if version.act_published_evidence is not None:
            if publication_id != version.root_publication_id or plan is None:
                raise ValueError("act_history_binding_scope")
            return version
        if version.reason == "grouped_stage_revision":
            if verify_grouped_stage_content(self._conn, version) != publication_id:
                raise ValueError("grouped_revision_slot_binding")
            return version
        if (
            plan is None
            or (
                plan.group_text
                if version.grouped_stage_evidence is not None
                else plan.rendered_text
            )
            != (version.rendered_text or "")
            or (
                publication_id != version.root_publication_id
                and plan.revision_of != version.root_publication_id
            )
        ):
            raise ValueError("content must match the frozen physical publication")
        if (
            version.grouped_stage_evidence is not None
            and publication_id != version.root_publication_id
        ):
            raise ValueError("grouped_stage_binding_scope")
        if version.change_snapshot_id is None:
            if verify_card_content(self._conn, version) != publication_id:
                raise ValueError("card content must bind its known physical operation")
        elif publication_id != version.root_publication_id and (
            publication.kind is not PublicationKind.AMENDMENTS_REVISION
            or plan.edit_message_id != root.message_id
            or root.status is not PublicationStatus.SENT
        ):
            raise ValueError("a revision content binding requires its original physical target")
        if (publication_id == version.root_publication_id) != (version.previous_version_id is None):
            raise ValueError("a content binding must distinguish the original and its revision")
        snapshots: dict[str, ContentSnapshot] = {}
        for _, identity in content_snapshot_refs(version):
            snapshot = self.get_content_snapshot(identity)
            if snapshot is not None:
                snapshots[identity] = snapshot
        mismatch = content_delivery_mismatch(version, snapshots, plan)
        if mismatch is not None:
            label = "semantic snapshot" if mismatch in ("body", "snapshot") else "delivery context"
            raise ValueError(f"content must match the frozen {label}")
        return version

    def bind_publication_content(self, publication_id: int, content_id: str) -> None:
        version = self._validate_publication_content_binding(publication_id, content_id)
        publication = self.publication_by_id(publication_id)
        assert publication is not None, "binding validation requires its physical publication"
        self._conn.execute(
            "INSERT INTO publication_content (publication_id, content_id) VALUES (?, ?)"
            " ON CONFLICT DO NOTHING",
            (publication_id, content_id),
        )
        if self.get_publication_content(publication_id) != version:
            raise ValueError("a publication cannot replace its immutable content binding")
        log.debug(
            "local publication content binding",
            extra=log_extra(
                bill_term=publication.term,
                bill_number=publication.number,
                content_id=content_id,
                publication_id=publication_id,
            ),
        )

    def reserve_grouped_stage_revision(
        self,
        version: ContentVersion,
        *,
        expected_content_id: str,
        expected_base_sha256: str,
        approved_sha256: str,
        expected_chat_id: int | None = None,
    ) -> int | None:
        """Reserve one approved inert edit against the confirmed shared target."""
        if expected_chat_id is not None and (
            self._conn.in_transaction
            or type(expected_chat_id) is not int
            or not -(2**52) < expected_chat_id < 0
        ):
            return None
        version = ContentVersion.model_validate_json(version.model_dump_json())
        evidence = version.grouped_stage_evidence
        ref = grouped_stage_revision_ref(version)
        if (
            ref is None
            or evidence is None
            or evidence.revision is None
            or version.previous_version_id != expected_content_id
            or version.rendered_text is None
            or hashlib.sha256(version.rendered_text.encode()).hexdigest() != approved_sha256
        ):
            return None
        with self.atomic():
            self._lock_grouped_stage_edit()
            root = self.publication_by_id(version.root_publication_id)
            if (
                root is None
                or root.kind is not PublicationKind.STATUS_UPDATE
                or root.status is not PublicationStatus.SENT
                or root.message_id != evidence.revision.target_message_id
            ):
                return None
            if expected_chat_id is not None:
                original = self.get_publication_content(version.root_publication_id)
                if original is None or read_grouped_stage_telegram_target(self._conn, original) != (
                    expected_chat_id,
                    evidence.revision.target_message_id,
                ):
                    return None
            prior = self.get_publication(
                root.term, root.number, PublicationKind.MESSAGE_PART, root.channel_id, ref=ref
            )
            retry_slot = None
            if prior is not None:
                if (
                    expected_chat_id is None
                    or prior.id is None
                    or not self._definitely_rejected_grouped_edit(prior)
                    or (saved := self.get_publication_content(prior.id)) is None
                    or saved.content_id != version.content_id
                ):
                    return None
                retry_slot = prior.id
            if self._grouped_stage_edit_conflict(root, ref, excluding_slot=retry_slot):
                return None
            current = self._confirmed_grouped_stage_base(
                root, excluding_content_id=version.content_id if retry_slot is not None else None
            )
            if (
                current is None
                or current.content_id != expected_content_id
                or current.rendered_text is None
                or hashlib.sha256(current.rendered_text.encode()).hexdigest()
                != expected_base_sha256
            ):
                return None
            if retry_slot is not None:
                return retry_slot
            bill = self.get_content_snapshot(version.bill_context_id)
            plan = grouped_stage_revision_plan(version, bill) if bill is not None else None
            if plan is None:
                return None
            publication_id = self._allocate_publication_id()
            self._conn.execute(
                "INSERT INTO publications"
                " (id, term, number, kind, status, channel_id, ref, created_at, delivery_json)"
                " VALUES (?, ?, ?, 'message_part', 'queued', ?, ?, ?, ?)",
                (
                    publication_id,
                    root.term,
                    root.number,
                    root.channel_id,
                    ref,
                    _iso(version.recorded_at),
                    plan.model_dump_json(),
                ),
            )
            self.save_content_version(version)
            self.bind_publication_content(publication_id, version.content_id)
            return publication_id

    def _lock_grouped_stage_edit(self) -> None:
        # Acquire SQLite write ownership before reading the confirmed base or target slots.
        lock = self._conn.execute("UPDATE publication_sequence SET last_id=last_id WHERE id=1")
        if lock.rowcount != 1:
            raise ValueError("grouped edit requires its publication sequence")

    @staticmethod
    def _definitely_rejected_grouped_edit(slot: Publication) -> bool:
        return (
            slot.status is PublicationStatus.FAILED
            and slot.attempts > 0
            and slot.error == _GROUPED_EDIT_REJECTED
        )

    def _grouped_stage_edit_conflict(
        self, root: Publication, ref: str, *, excluding_slot: int | None = None
    ) -> bool:
        group_pattern = f"{GROUPED_STAGE_REVISION_REF_PREFIX}{root.id}/*"
        return (
            self._conn.execute(
                """
                SELECT 1 FROM publications p WHERE p.channel_id=? AND (? IS NULL OR p.id!=?)
                AND (
                    (p.kind='message_part' AND p.ref=?) OR (
                        p.status IN ('queued','failed','pending','unknown')
                        AND ((p.kind='message_part' AND p.ref GLOB ?) OR (
                            p.delivery_json IS NOT NULL AND CASE
                                WHEN NOT json_valid(p.delivery_json) THEN 1
                                WHEN json_type(p.delivery_json)!='object' THEN 1
                                ELSE CAST(json_extract(p.delivery_json,'$.edit_message_id')
                                    AS INTEGER)=? END
                        ))
                        AND NOT (p.status='failed' AND p.attempts>0 AND p.error IS ?
                            AND p.kind='message_part' AND p.ref GLOB ? AND EXISTS (
                                SELECT 1 FROM publication_content b JOIN content_versions v
                                ON v.content_id=b.content_id
                                WHERE b.publication_id=p.id AND v.root_publication_id=?
                            )
                        )
                    )
                ) LIMIT 1
                """,
                (
                    root.channel_id,
                    excluding_slot,
                    excluding_slot,
                    ref,
                    group_pattern,
                    root.message_id,
                    _GROUPED_EDIT_REJECTED,
                    group_pattern,
                    root.id,
                ),
            ).fetchone()
            is not None
        )

    def claim_grouped_stage_edit(
        self,
        publication_id: int,
        *,
        content_id: str,
        base_content_id: str,
        approved_sha256: str,
        chat_id: int,
        allow_rejected: bool = False,
    ) -> GroupedEditClaim | None:
        """Claim the exact approved slot, requiring explicit approval after a rejection."""
        if self._conn.in_transaction or type(chat_id) is not int or not -(2**52) < chat_id < 0:
            return None
        with self.atomic():
            self._lock_grouped_stage_edit()
            slot = self.publication_by_id(publication_id)
            if (
                slot is None
                or not is_grouped_stage_revision_slot(slot)
                or not (
                    (slot.status is PublicationStatus.QUEUED and not slot.attempts)
                    or (allow_rejected and self._definitely_rejected_grouped_edit(slot))
                )
                or slot.delivery is None
                or (target := slot.delivery.edit_message_id) is None
                or self.delivery_resolutions(publication_id)
                or hashlib.sha256(slot.delivery.rendered_text.encode()).hexdigest()
                != approved_sha256
            ):
                return None
            claim = GroupedEditClaim(
                publication_id,
                content_id,
                base_content_id,
                chat_id,
                target,
                slot.delivery.rendered_text,
            )
            if not self._grouped_stage_edit_matches(claim, slot):
                return None
            self.mark_publication(publication_id, PublicationStatus.PENDING, count_attempt=False)
            return claim

    def grouped_stage_edit_matches(self, claim: GroupedEditClaim) -> bool:
        if self._conn.in_transaction:
            return False
        with self.atomic():
            slot = self.publication_by_id(claim.publication_id)
            return (
                slot is not None
                and slot.status is PublicationStatus.PENDING
                and self._grouped_stage_edit_matches(claim, slot)
            )

    def _grouped_stage_edit_matches(self, claim: GroupedEditClaim, slot: Publication) -> bool:
        if (
            not is_grouped_stage_revision_slot(slot)
            or slot.delivery is None
            or slot.delivery.edit_message_id != claim.message_id
            or slot.delivery.rendered_text != claim.html
        ):
            return False
        try:
            version = self.get_publication_content(claim.publication_id)
            if (
                version is None
                or version.reason != "grouped_stage_revision"
                or version.content_id != claim.content_id
                or version.previous_version_id != claim.base_content_id
            ):
                return False
            root = self.publication_by_id(version.root_publication_id)
            original = self.get_publication_content(version.root_publication_id)
            ref = slot.ref
            if (
                root is None
                or original is None
                or ref is None
                or ref
                not in (
                    grouped_stage_revision_ref(version),
                    grouped_stage_revision_ref(version, candidate_scoped=True),
                )
                or read_grouped_stage_telegram_target(self._conn, original)
                != (claim.chat_id, claim.message_id)
                or self._grouped_stage_edit_conflict(root, ref, excluding_slot=claim.publication_id)
            ):
                return False
            base = self._confirmed_grouped_stage_base(root, excluding_content_id=claim.content_id)
            return base is not None and base.content_id == claim.base_content_id
        except ValueError:
            return False

    def finish_grouped_stage_edit(
        self, claim: GroupedEditClaim, outcome: VerifiedEditOutcome | None, *, at: datetime
    ) -> PublicationStatus | None:
        """Commit a matching receipt, a definite rejection, or uncertainty on the claimed slot."""
        if self._conn.in_transaction:
            raise StateCheckpointError("Grouped edit completion requires its own transaction")
        with self.atomic():
            self._lock_grouped_stage_edit()
            owner = self._conn.execute(
                "SELECT 1 FROM publications p JOIN publication_content c ON c.publication_id=p.id"
                " WHERE p.id=? AND p.kind='message_part' AND p.ref GLOB ?"
                " AND p.status='pending' AND c.content_id=?",
                (claim.publication_id, f"{GROUPED_STAGE_REVISION_REF_PREFIX}*", claim.content_id),
            ).fetchone()
            if owner is None:
                return None
            slot = self.publication_by_id(claim.publication_id)
            if slot is not None and self._grouped_stage_edit_matches(claim, slot):
                if isinstance(outcome, VerifiedEditRejection):
                    self.mark_publication(
                        claim.publication_id,
                        PublicationStatus.FAILED,
                        error=_GROUPED_EDIT_REJECTED,
                    )
                    return PublicationStatus.FAILED
                if outcome is not None and (
                    type(outcome.chat_id) is int
                    and type(outcome.message_id) is int
                    and (outcome.chat_id, outcome.message_id) == (claim.chat_id, claim.message_id)
                ):
                    self.mark_publication(
                        claim.publication_id,
                        PublicationStatus.SENT,
                        message_id=claim.message_id,
                        sent_at=at,
                    )
                    return PublicationStatus.SENT
            self.mark_publication(
                claim.publication_id,
                PublicationStatus.UNKNOWN,
                error="The grouped edit outcome could not be verified",
                count_attempt=False,
            )
            return PublicationStatus.UNKNOWN

    def resolve_grouped_stage_edit(self, resolution: GroupedEditDeliveryResolution) -> bool:
        """Settle one uncertain group edit with an exact recorded operator attestation."""
        if self._conn.in_transaction:
            return False
        raw = GroupedEditResolutionEnvelope(
            profile="grouped-edit-resolution-v1", resolution=resolution
        ).model_dump_json()
        audit = GroupedEditResolutionEnvelope.model_validate_json(raw).resolution
        with self.atomic():
            self._lock_grouped_stage_edit()
            slot = self.publication_by_id(audit.publication_id)
            if (
                slot is None
                or not is_grouped_stage_revision_slot(slot)
                or slot.status is not PublicationStatus.UNKNOWN
                or slot.delivery is None
                or (audit.term, audit.number, audit.kind, audit.channel_id)
                != (slot.term, slot.number, slot.kind, slot.channel_id)
                or (audit.observed_at or audit.resolved_at) < slot.created_at
                or hashlib.sha256(slot.delivery.rendered_text.encode()).hexdigest()
                != audit.rendered_sha256
                or not recorded_command_matches(self._conn, audit.command)
                or self._conn.execute(
                    "SELECT 1 FROM delivery_resolutions WHERE command_update_id=?",
                    (audit.command.update_id,),
                ).fetchone()
                is not None
            ):
                return False
            claim = GroupedEditClaim(
                audit.publication_id,
                audit.content_id,
                audit.base_content_id,
                audit.telegram_chat_id,
                audit.target_message_id,
                slot.delivery.rendered_text,
            )
            if not self._grouped_stage_edit_matches(claim, slot):
                return False
            self.mark_publication(
                audit.publication_id,
                audit.status,
                message_id=audit.message_id,
                sent_at=audit.resolved_at if audit.action == "confirm" else None,
                count_attempt=False,
            )
            self._conn.execute(
                "INSERT INTO delivery_resolutions"
                " (publication_id, command_update_id, resolution_json)"
                " VALUES (?, ?, ?)",
                (audit.publication_id, audit.command.update_id, raw),
            )
            if self.get_publication_content(audit.publication_id) is None:
                raise StateCheckpointError("Grouped edit resolution lost its content binding")
            return True

    def _confirmed_grouped_stage_base(
        self, root: Publication, *, excluding_content_id: str | None = None
    ) -> ContentVersion | None:
        if root.id is None or root.message_id is None:
            return None
        versions = {
            version.content_id: version
            for version in self.list_content_versions(root.id)
            if version.content_id != excluding_content_id
        }
        if not versions or any(
            version.grouped_stage_evidence is None
            or version.grouped_stage_evidence.render_witness is None
            for version in versions.values()
        ):
            return None
        bindings = self._conn.execute(
            "SELECT b.content_id, p.id, p.status, p.attempts, p.error FROM publication_content b"
            " JOIN content_versions v ON v.content_id=b.content_id"
            " JOIN publications p ON p.id=b.publication_id"
            " WHERE v.root_publication_id=? AND (? IS NULL OR b.content_id!=?)",
            (root.id, excluding_content_id, excluding_content_id),
        ).fetchall()
        if len(bindings) != len(versions) or any(
            row["status"] != "sent"
            and not (
                row["status"] == "failed"
                and row["attempts"] > 0
                and row["error"] == _GROUPED_EDIT_REJECTED
            )
            for row in bindings
        ):
            return None
        if {row["content_id"] for row in bindings} != versions.keys():
            return None
        confirmed = {row["content_id"] for row in bindings if row["status"] == "sent"}
        current = confirmed_content_version(
            {identity: version.previous_version_id for identity, version in versions.items()},
            confirmed,
        ).current_id
        if current is None:
            return None
        base = versions[current]
        evidence = base.grouped_stage_evidence
        assert evidence is not None, "the supported history has exact grouped evidence"
        known_receipts = {row["id"] for row in bindings if row["status"] == "sent"} | {
            member.publication_id for member in evidence.members
        }
        receipts = self._conn.execute(
            "SELECT id FROM publications WHERE channel_id=? AND status='sent' AND message_id=?",
            (root.channel_id, root.message_id),
        ).fetchall()
        return base if {row["id"] for row in receipts} == known_receipts else None

    def get_publication_content(self, publication_id: int) -> ContentVersion | None:
        row = self._conn.execute(
            "SELECT v.* FROM publication_content b LEFT JOIN content_versions v"
            " ON v.content_id=b.content_id WHERE b.publication_id=?",
            (publication_id,),
        ).fetchone()
        if row is None:
            return None
        if row["content_id"] is None:
            raise ValueError("a publication content binding has lost its version")
        version = read_content_version(row)
        card_physical_id = self._verify_retained_content_version(version)
        if (
            version.direct_stage_evidence is not None
            and version.direct_stage_evidence.multipart is not None
        ):
            stage = verify_direct_stage_content(self._conn, version, require_bound=True)
            if stage is None or publication_id not in stage.bindings:
                raise ValueError("direct_stage_binding_scope")
            return version
        if (
            version.direct_stage_evidence is not None
            or (version.grouped_stage_evidence is not None and version.reason == "original")
        ) and version.root_publication_id != publication_id:
            raise ValueError("direct_stage_binding_scope")
        if version.digest_profile is not None:
            verify_digest_content(self._conn, version, require_bound=True)
            return version
        if version.sitting_profile is not None:
            plan = verify_sitting_content(self._conn, version, require_bound=True)
            if plan is None or publication_id not in plan.bindings:
                raise ValueError("sitting_history_binding_scope")
            return version
        if version.act_published_evidence is not None:
            verify_act_published_content(self._conn, version, require_bound=True)
            return version
        if version.original_delivery_evidence is not None:
            original = verify_original_delivery_content(self._conn, version, require_bound=True)
            if original is None or publication_id not in original.bindings:
                raise ValueError("original_delivery_binding_scope")
            return version
        if version.multipart_card_revision is not None:
            edition = verify_multipart_card_revision(self._conn, version, require_bound=True)
            if edition is None or publication_id not in edition.bindings:
                raise ValueError("multipart_card_binding_scope")
            return version
        if card_physical_id is not None and card_physical_id != publication_id:
            raise ValueError("card content binding differs from its physical operation")
        return version

    def list_content_publications(
        self, root_publication_id: int, *, content_id: str | None = None
    ) -> list[Publication]:
        if (
            content_id is None
            and self._conn.execute(
                "SELECT 1 FROM content_versions WHERE root_publication_id=? "
                "AND change_snapshot_id IS NULL AND bill_snapshot_id IS NOT NULL "
                "AND json_type(version_json, '$.act_published_evidence') IS NULL "
                "AND json_type(version_json, '$.original_delivery_evidence') IS NULL",
                (root_publication_id,),
            ).fetchone()
        ):
            return [
                self._row_to_publication(row)
                for row in card_publication_rows(self._conn, root_publication_id)
            ]
        rows = self._conn.execute(
            "SELECT p.* FROM publications p"
            " JOIN publication_content binding ON binding.publication_id=p.id"
            " JOIN content_versions version ON version.content_id=binding.content_id"
            " WHERE version.root_publication_id=? AND (? IS NULL OR version.content_id=?)"
            " ORDER BY p.id",
            (root_publication_id, content_id, content_id),
        )
        return [self._row_to_publication(row) for row in rows]

    def save_source_catalog_snapshot(
        self, snapshot: SourceCatalogSnapshot, *, recorded_at: datetime
    ) -> None:
        stamp = SourceCatalogSnapshot.timestamp(recorded_at)
        with self.atomic():
            saved = self.get_source_catalog_snapshot(snapshot.catalog_id)
            if saved is not None:
                if saved.model_dump(exclude={"recorded_at"}) != snapshot.model_dump(
                    exclude={"recorded_at"}
                ):
                    raise ValueError("immutable catalog snapshot cannot be replaced")
                return
            record = SourceCatalogSnapshot.model_validate_json(
                snapshot.model_copy(update={"recorded_at": stamp}).model_dump_json()
            )
            self._conn.execute(
                "INSERT INTO source_catalog_snapshots ("
                + ",".join((*CATALOG_COLUMNS, "record_json"))
                + ") VALUES (?,?,?,?,?,?)",
                (*catalog_values(record), record.model_dump_json()),
            )
            if self.get_source_catalog_snapshot(record.catalog_id) != record:
                raise SourceCatalogRowError("catalog snapshot failed exact persistence")

    def get_source_catalog_snapshot(self, catalog_id: str) -> SourceCatalogSnapshot | None:
        row = self._conn.execute(
            "SELECT * FROM source_catalog_snapshots WHERE catalog_id=?", (catalog_id,)
        ).fetchone()
        return read_catalog(row) if row is not None else None

    def save_source_catalog_attempt(
        self, attempt: SourceCatalogAttempt, *, recorded_at: datetime
    ) -> None:
        stamp = SourceCatalogAttempt.timestamp(recorded_at)
        with self.atomic():
            saved = self.get_source_catalog_attempt(attempt.attempt_id)
            if saved is not None:
                if saved.model_dump(exclude={"recorded_at"}) != attempt.model_dump(
                    exclude={"recorded_at"}
                ):
                    raise ValueError("immutable catalog attempt cannot be replaced")
                return
            record = SourceCatalogAttempt.model_validate_json(
                attempt.model_copy(update={"recorded_at": stamp}).model_dump_json()
            )
            self._conn.execute(
                "INSERT INTO source_catalog_attempts ("
                + ",".join((*ATTEMPT_COLUMNS, "record_json"))
                + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*attempt_values(record), record.model_dump_json()),
            )
            for ordinal, parent in enumerate(record.parents):
                self._conn.execute(
                    "INSERT INTO source_catalog_attempt_parents VALUES (?,?,?,?,?)",
                    (
                        record.attempt_id,
                        ordinal,
                        parent.artifact_sha256,
                        parent.artifact_read_id,
                        parent.extraction_id,
                    ),
                )
            if self.get_source_catalog_attempt(record.attempt_id) != record:
                raise SourceCatalogRowError("catalog attempt failed exact persistence")

    def get_source_catalog_attempt(self, attempt_id: str) -> SourceCatalogAttempt | None:
        row = self._conn.execute(
            "SELECT * FROM source_catalog_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        return read_attempt(self._conn, row) if row is not None else None

    def list_source_catalog_attempts(self) -> list[SourceCatalogAttempt]:
        return [
            read_attempt(self._conn, row)
            for row in self._conn.execute(
                "SELECT * FROM source_catalog_attempts ORDER BY attempt_id"
            )
        ]

    def save_source_metadata_omission(
        self, omission: SourceMetadataOmission, *, recorded_at: datetime
    ) -> None:
        stamp = SourceMetadataOmission.aware_time(recorded_at)
        with self.atomic():
            saved = self.get_source_metadata_omission(omission.omission_id)
            if saved is not None:
                if saved.model_dump(exclude={"recorded_at"}) != omission.model_dump(
                    exclude={"recorded_at"}
                ):
                    raise ValueError("immutable source omission cannot be replaced")
                return
            record = SourceMetadataOmission.model_validate_json(
                omission.model_copy(update={"recorded_at": stamp}).model_dump_json()
            )
            self._conn.execute(
                "INSERT INTO source_metadata_omissions ("
                + ",".join((*SOURCE_REFERENCE_COLUMNS, "record_json"))
                + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*omission_values(record), record.model_dump_json()),
            )
            for index, parent in enumerate(record.parents):
                self._conn.execute(
                    "INSERT INTO source_omission_parents VALUES (?,?,?,?,?,?)",
                    (
                        record.omission_id,
                        index,
                        parent.artifact_sha256,
                        parent.artifact_read_id,
                        parent.extraction_id,
                        parent.source_observation_id,
                    ),
                )
            if self.get_source_metadata_omission(record.omission_id) != record:
                raise SourceOmissionRowError("source omission failed exact persistence")

    def get_source_metadata_omission(self, omission_id: str) -> SourceMetadataOmission | None:
        row = self._conn.execute(
            "SELECT * FROM source_metadata_omissions WHERE omission_id=?", (omission_id,)
        ).fetchone()
        return read_source_omission(self._conn, row) if row is not None else None

    def list_source_metadata_omissions(self) -> list[SourceMetadataOmission]:
        return [
            read_source_omission(self._conn, row)
            for row in self._conn.execute(
                "SELECT * FROM source_metadata_omissions ORDER BY omission_id"
            )
        ]

    def save_physical_inventory(
        self,
        metadata: tuple[PhysicalMetadata, ...] = (),
        captures: tuple[PhysicalCapture, ...] = (),
        *,
        recorded_at: datetime,
    ) -> None:
        stamp = physical_recorded_at(recorded_at)
        first_times: dict[str, datetime] = {}
        existing_reader = PhysicalInventoryReader(self._conn)
        for item in metadata:
            row = self._conn.execute(
                "SELECT 1 FROM physical_metadata WHERE metadata_id=?", (item.metadata_id,)
            ).fetchone()
            first = existing_reader.read(item.metadata_id).recorded_at if row else recorded_at
            assert first is not None, "the physical reader guarantees persisted recording times"
            first_times[item.metadata_id] = first
        records = tuple(
            PhysicalMetadata.model_validate_json(
                item.model_copy(
                    update={"recorded_at": first_times[item.metadata_id]}
                ).model_dump_json()
            )
            for item in metadata
        )
        receipts = tuple(
            PhysicalCapture.model_validate_json(
                item.model_copy(update={"recorded_at": recorded_at}).model_dump_json()
            )
            for item in captures
        )
        with self.atomic():
            for record in records:
                self._conn.execute(
                    "INSERT INTO physical_metadata VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (
                        record.metadata_id,
                        record.kind,
                        record.body_sha256,
                        record.header_id,
                        record.coverage,
                        stamp,
                        record.model_dump_json(),
                    ),
                )
            reader = PhysicalInventoryReader(self._conn)
            for record in records:
                saved = reader.read(record.metadata_id)
                if saved.model_dump(exclude={"recorded_at"}) != record.model_dump(
                    exclude={"recorded_at"}
                ):
                    raise ValueError("immutable physical metadata cannot be replaced")
            for capture in receipts:
                existing = self._conn.execute(
                    "SELECT 1 FROM physical_captures WHERE capture_id=?", (capture.capture_id,)
                ).fetchone()
                if existing is None:
                    reader.verify_capture(capture)
                self._conn.execute(
                    "INSERT INTO physical_captures VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                    (
                        capture.capture_id,
                        capture.artifact_sha256,
                        capture.recipe_id,
                        capture.inventory_id,
                        capture.execution_id,
                        capture.extraction_id,
                        stamp,
                        capture.model_dump_json(),
                    ),
                )
                row = self._conn.execute(
                    "SELECT * FROM physical_captures WHERE capture_id=?", (capture.capture_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(
                        "physical capture conflicts with a different immutable identity"
                    )
                saved_capture = reader.capture(row)
                if saved_capture.model_dump(exclude={"recorded_at"}) != capture.model_dump(
                    exclude={"recorded_at"}
                ):
                    raise ValueError("immutable physical capture cannot be replaced")

    def save_physical_metadata(self, metadata: PhysicalMetadata, *, recorded_at: datetime) -> None:
        self.save_physical_inventory((metadata,), recorded_at=recorded_at)

    def get_physical_metadata(self, metadata_id: str) -> PhysicalMetadata | None:
        if (
            self._conn.execute(
                "SELECT 1 FROM physical_metadata WHERE metadata_id=?", (metadata_id,)
            ).fetchone()
            is None
        ):
            return None
        return PhysicalInventoryReader(self._conn).read(metadata_id)

    def save_physical_capture(self, capture: PhysicalCapture, *, recorded_at: datetime) -> None:
        self.save_physical_inventory(captures=(capture,), recorded_at=recorded_at)

    def get_physical_capture(self, capture_id: str) -> PhysicalCapture | None:
        row = self._conn.execute(
            "SELECT * FROM physical_captures WHERE capture_id=?", (capture_id,)
        ).fetchone()
        return PhysicalInventoryReader(self._conn).capture(row) if row is not None else None

    def iter_physical_captures(
        self, artifact_sha256: str | None = None
    ) -> Iterator[PhysicalCapture]:
        rows = self._conn.execute(
            "SELECT * FROM physical_captures WHERE (? IS NULL OR artifact_sha256=?)"
            " ORDER BY capture_id",
            (artifact_sha256, artifact_sha256),
        )
        reader = PhysicalInventoryReader(self._conn)
        for row in rows:
            yield reader.capture(row)

    def save_senate_inspection(
        self,
        record: SenateInspectionRecord,
        links: tuple[SenateInspectionLink, ...] = (),
        *,
        recorded_at: datetime,
    ) -> None:
        record = SenateInspectionRecord.model_validate_json(
            record.model_dump_json(exclude={"recorded_at"})
        )
        if recorded_at.utcoffset() is None:
            raise ValueError("Senate inspection recording time requires a timezone")
        record = SenateInspectionRecord.model_validate(
            {**record.model_dump(), "recorded_at": recorded_at}
        )
        links = tuple(
            SenateInspectionLink.model_validate_json(link.model_dump_json()) for link in links
        )
        verify_senate_inspection(self._conn, record, links)
        with self.atomic():
            self._conn.execute(
                "INSERT INTO senate_inspections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT DO NOTHING",
                (
                    record.inspection_id,
                    record.semantic_sha256,
                    record.owner.term,
                    record.owner.number,
                    record.resolution_print.term,
                    record.resolution_print.number,
                    record.coverage,
                    record.descriptor_count,
                    record.link_count,
                    record.links_sha256,
                    _utc_iso(recorded_at),
                    record.model_dump_json(),
                ),
            )
            for link in links:
                self._conn.execute(
                    "INSERT INTO senate_inspection_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT DO NOTHING",
                    (
                        record.inspection_id,
                        link.descriptor_index,
                        link.descriptor_sha256,
                        link.requested_url_sha256,
                        link.observation_id,
                        link.selection_id,
                        link.artifact_sha256,
                        link.artifact_bytes,
                        link.reader_text_sha256,
                        link.artifact_read_id,
                        link.extraction_id,
                    ),
                )
            saved = self.get_senate_inspection(record.inspection_id)
            if (
                saved is None
                or saved.model_copy(update={"recorded_at": record.recorded_at}) != record
            ):
                raise ValueError("immutable Senate inspection metadata cannot be replaced")

    def get_senate_inspection(self, inspection_id: str) -> SenateInspectionRecord | None:
        row = self._conn.execute(
            "SELECT * FROM senate_inspections WHERE inspection_id=?", (inspection_id,)
        ).fetchone()
        if (
            row is None
            and self._conn.execute(
                "SELECT 1 FROM senate_inspection_sources WHERE inspection_id=? LIMIT 1",
                (inspection_id,),
            ).fetchone()
            is not None
        ):
            raise SenateInspectionRowError("senate_inspection_parent_mismatch")
        return read_senate_inspection(self._conn, row) if row is not None else None

    def iter_senate_inspections(self, term: int, number: str) -> Iterator[SenateInspectionRecord]:
        for row in self._conn.execute(
            "SELECT * FROM senate_inspections WHERE owner_term=? AND owner_number=?"
            " ORDER BY inspection_id",
            (term, number),
        ):
            yield read_senate_inspection(self._conn, row)

    def list_senate_inspection_links(self, inspection_id: str) -> tuple[SenateInspectionLink, ...]:
        record = self.get_senate_inspection(inspection_id)
        return senate_inspection_links(self._conn, inspection_id) if record is not None else ()

    def save_rcl_letter_use(self, use: RclLetterUse, *, recorded_at: datetime) -> None:
        use = RclLetterUse.model_validate_json(use.model_dump_json(exclude={"recorded_at"}))
        if recorded_at.utcoffset() is None:
            raise ValueError("letter-use recording time requires a timezone")
        record = RclLetterUseRecord(**use.model_dump(), recorded_at=recorded_at.astimezone(UTC))
        verify_rcl_letter_use_parents(self._conn, record)
        official, physical = record.payload.official, record.payload.physical
        self._conn.execute(
            "INSERT INTO rcl_letter_uses VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (
                record.use_id,
                official.project_id,
                official.stage_id,
                official.stage_number,
                official.letter_id,
                record.payload_state,
                physical.artifact_sha256,
                physical.artifact_read_id,
                physical.extraction_id,
                record.semantic_sha256,
                _utc_iso(record.recorded_at),
                record.model_dump_json(),
            ),
        )
        saved = self.get_rcl_letter_use(record.use_id)
        if saved is None or saved.model_copy(update={"recorded_at": record.recorded_at}) != record:
            raise ValueError("immutable letter-use metadata cannot be replaced")

    def get_rcl_letter_use(self, use_id: str) -> RclLetterUseRecord | None:
        row = self._conn.execute(
            "SELECT * FROM rcl_letter_uses WHERE use_id=?", (use_id,)
        ).fetchone()
        return read_rcl_letter_use(self._conn, row) if row is not None else None

    def iter_rcl_letter_uses(self, project_id: int) -> Iterator[RclLetterUseRecord]:
        for row in self._conn.execute(
            "SELECT * FROM rcl_letter_uses WHERE project_id=? ORDER BY stage_id,letter_id,use_id",
            (project_id,),
        ):
            yield read_rcl_letter_use(self._conn, row)

    def save_extraction_result(self, result: ExtractionResult, *, recorded_at: datetime) -> None:
        result = ExtractionResult.model_validate_json(result.model_dump_json())
        if recorded_at.utcoffset() is None:
            raise ValueError("extraction recording time requires a timezone")
        if self.get_artifact_metadata(result.artifact_sha256) is None:
            raise ValueError("an extraction requires recorded complete-body metadata")
        self._conn.execute(
            "INSERT INTO extraction_results"
            " (extraction_id, artifact_sha256, recorded_at, result_json)"
            " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                result.result_id,
                result.artifact_sha256,
                _utc_iso(recorded_at),
                result.model_dump_json(),
            ),
        )
        if self.get_extraction_result(result.result_id) != result:
            raise ValueError("an immutable extraction result cannot be replaced")
        log.debug(
            "local extraction result write",
            extra=log_extra(extraction_id=result.result_id, artifact_sha256=result.artifact_sha256),
        )

    def get_extraction_result(self, result_id: str) -> ExtractionResult | None:
        row = self._conn.execute(
            "SELECT * FROM extraction_results WHERE extraction_id=?", (result_id,)
        ).fetchone()
        if row is None:
            return None
        return read_extraction_result(row)

    def save_selection_extraction(self, reference: SelectionExtractionReference) -> None:
        reference = SelectionExtractionReference.model_validate_json(reference.model_dump_json())
        selection = self.get_document_selection(reference.selection_id)
        extraction = self.get_extraction_result(reference.extraction_id)
        if selection is None or extraction is None:
            raise ValueError("an extraction link requires its recorded parents")
        sources = {
            part.source_sha256
            for part in (*selection.selection.parts, *selection.selection.observed_parts)
        }
        if (
            extraction.artifact_sha256 != reference.artifact_sha256
            or reference.artifact_sha256 not in sources
        ):
            raise ValueError("an extraction link requires the selection's exact outer body")
        self._conn.execute(
            "INSERT INTO selection_extractions (selection_id, extraction_id)"
            " VALUES (?, ?) ON CONFLICT DO NOTHING",
            (reference.selection_id, reference.extraction_id),
        )
        if reference not in self.list_selection_extractions(reference.selection_id):
            raise ValueError("an immutable extraction link cannot be rebound")
        log.debug(
            "local selection extraction binding",
            extra=log_extra(
                selection_id=reference.selection_id,
                extraction_id=reference.extraction_id,
                artifact_sha256=reference.artifact_sha256,
            ),
        )

    def list_selection_extractions(self, selection_id: str) -> list[SelectionExtractionReference]:
        rows = self._conn.execute(
            "SELECT link.*, result.artifact_sha256 FROM selection_extractions link"
            " JOIN extraction_results result ON result.extraction_id=link.extraction_id"
            " WHERE link.selection_id=? ORDER BY link.rowid",
            (selection_id,),
        )
        return [SelectionExtractionReference.model_validate(dict(row)) for row in rows]

    def save_artifact_metadata(self, artifact: ArtifactMetadata) -> None:
        artifact = ArtifactMetadata.model_validate_json(artifact.model_dump_json())
        self._conn.execute(
            "INSERT INTO artifacts (sha256, byte_size, recorded_at) VALUES (?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (artifact.sha256, artifact.byte_size, _utc_iso(artifact.recorded_at)),
        )
        saved = self.get_artifact_metadata(artifact.sha256)
        assert saved is not None, "the insert retains metadata under its complete-body SHA"
        if saved != artifact.model_copy(update={"recorded_at": saved.recorded_at}):
            raise ValueError("an immutable artifact identity cannot change its byte size")
        log.debug("local artifact metadata write", extra=log_extra(artifact_sha256=artifact.sha256))

    def get_artifact_metadata(self, sha256: str) -> ArtifactMetadata | None:
        row = self._conn.execute("SELECT * FROM artifacts WHERE sha256=?", (sha256,)).fetchone()
        return (
            ArtifactMetadata(
                sha256=row["sha256"],
                byte_size=row["byte_size"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
            )
            if row
            else None
        )

    def save_artifact_read(self, receipt: ArtifactReadReceipt) -> None:
        receipt = ArtifactReadReceipt.model_validate_json(receipt.model_dump_json())
        artifact = self.get_artifact_metadata(receipt.sha256)
        if artifact is None or artifact.byte_size != receipt.byte_size:
            raise ValueError("an artifact read requires matching complete-body metadata")
        self._conn.execute(
            "INSERT INTO artifact_reads"
            " (read_id, requested_url, artifact_sha256, byte_size, recorded_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                receipt.read_id,
                receipt.requested_url,
                receipt.sha256,
                receipt.byte_size,
                _utc_iso(receipt.recorded_at),
            ),
        )
        saved = self.get_artifact_read(receipt.read_id)
        assert saved is not None, "the insert retains the receipt under its immutable read identity"
        if saved != receipt.model_copy(update={"recorded_at": saved.recorded_at}):
            raise ValueError("an immutable artifact read receipt cannot be replaced")
        log.debug(
            "local artifact read write",
            extra=log_extra(artifact_sha256=receipt.sha256, artifact_read_id=receipt.read_id),
        )

    def get_artifact_read(self, read_id: str) -> ArtifactReadReceipt | None:
        row = self._conn.execute(
            "SELECT * FROM artifact_reads WHERE read_id=?", (read_id,)
        ).fetchone()
        return self._artifact_read(row) if row is not None else None

    def list_artifact_reads(
        self, *, sha256: str | None = None, requested_url: str | None = None
    ) -> list[ArtifactReadReceipt]:
        clauses: list[str] = []
        parameters: list[str] = []
        if sha256 is not None:
            clauses.append("artifact_sha256=?")
            parameters.append(sha256)
        if requested_url is not None:
            clauses.append("requested_url=?")
            parameters.append(requested_url)
        query = "SELECT * FROM artifact_reads"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        return [
            self._artifact_read(row)
            for row in self._conn.execute(query + " ORDER BY rowid", parameters)
        ]

    @staticmethod
    def _artifact_read(row: sqlite3.Row) -> ArtifactReadReceipt:
        receipt = ArtifactReadReceipt(
            requested_url=row["requested_url"],
            sha256=row["artifact_sha256"],
            byte_size=row["byte_size"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )
        if receipt.read_id != row["read_id"]:
            raise ValueError("artifact read columns differ from the recorded identity")
        return receipt

    def save_selection_artifact(self, reference: SelectionArtifactReference) -> None:
        reference = SelectionArtifactReference.model_validate_json(reference.model_dump_json())
        selection = self.get_document_selection(reference.selection_id)
        if selection is None:
            raise ValueError("an artifact reference requires a recorded selection")
        parts = (
            selection.selection.parts
            if reference.part_scope == "parts"
            else selection.selection.observed_parts
        )
        if reference.part_index >= len(parts):
            raise ValueError("an artifact reference points outside the selected part scope")
        part = parts[reference.part_index]
        expected = part.source_sha256
        if reference.artifact_sha256 != expected:
            raise ValueError("an artifact reference must match the recorded outer body identity")
        if self.get_artifact_metadata(reference.artifact_sha256) is None:
            raise ValueError("an artifact reference requires known complete-body metadata")
        self._conn.execute(
            "INSERT INTO selection_artifacts"
            " (selection_id, part_scope, part_index, artifact_sha256) VALUES (?, ?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (
                reference.selection_id,
                reference.part_scope,
                reference.part_index,
                reference.artifact_sha256,
            ),
        )
        saved = self._conn.execute(
            "SELECT artifact_sha256 FROM selection_artifacts"
            " WHERE selection_id=? AND part_scope=? AND part_index=?",
            (reference.selection_id, reference.part_scope, reference.part_index),
        ).fetchone()
        if saved is None or saved[0] != reference.artifact_sha256:
            raise ValueError("an immutable selected part cannot be rebound to different bytes")
        log.debug(
            "local selection artifact binding",
            extra=log_extra(
                selection_id=reference.selection_id, artifact_sha256=reference.artifact_sha256
            ),
        )

    def list_selection_artifacts(self, selection_id: str) -> list[SelectionArtifactReference]:
        return [
            SelectionArtifactReference(
                selection_id=row["selection_id"],
                part_scope=row["part_scope"],
                part_index=row["part_index"],
                artifact_sha256=row["artifact_sha256"],
            )
            for row in self._conn.execute(
                "SELECT * FROM selection_artifacts WHERE selection_id=?"
                " ORDER BY CASE part_scope WHEN 'parts' THEN 0 ELSE 1 END, part_index",
                (selection_id,),
            )
        ]

    def save_result_application(self, application: ResultApplication) -> None:
        validate_projected_application(self._conn, application)
        identity = (
            application.result_id,
            application.term,
            application.number,
            application.generation,
            application.phase,
        )
        self._conn.execute(
            "INSERT INTO result_applications"
            " (result_id, term, number, generation, phase, application_json)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (*identity, application.model_dump_json()),
        )
        target = application.target
        row = self._conn.execute(
            "SELECT application_json FROM result_applications WHERE result_id = ? AND term = ?"
            " AND number = ? AND generation IS ? AND phase = ?"
            " AND json_extract(application_json, '$.target.kind') IS ?"
            " AND json_extract(application_json, '$.target.ref') IS ?",
            (*identity, target.kind if target else None, target.ref if target else None),
        ).fetchone()
        if (
            row is None
            or ResultApplication.model_validate_json(row[0]).model_copy(
                update={"created_at": application.created_at}
            )
            != application
        ):
            raise ValueError("an immutable result application cannot be replaced")
        log.debug(
            "local result application write",
            extra=log_extra(
                bill_term=application.term,
                bill_number=application.number,
                computation_id=application.result_id,
                application_phase=application.phase,
                application_generation=application.generation,
                application_target_kind=target.kind if target else None,
                application_target_ref=target.ref if target else None,
            ),
        )

    def get_primary_projection_application(
        self, result_id: str, term: int, number: str, generation: int
    ) -> ResultApplication | None:
        row = self._conn.execute(
            "SELECT * FROM result_applications WHERE result_id=? AND term=? AND number=?"
            " AND generation=? AND phase='projection'",
            (result_id, term, number, generation),
        ).fetchone()
        if row is None:
            return None
        application = ResultApplication.model_validate_json(row["application_json"])
        if (
            application.result_id,
            application.term,
            application.number,
            application.generation,
            application.phase,
            application.target,
        ) != (result_id, term, number, generation, "projection", None):
            raise ValueError("the primary projection key differs from its immutable receipt")
        validate_projected_application(self._conn, application)
        return application

    def find_projection_application_ids(
        self, result_id: str, target: ResultApplicationTarget
    ) -> tuple[int, ...]:
        return find_projection_application_ids(self._conn, result_id, target)

    def list_result_applications(self, term: int, number: str) -> list[ResultApplication]:
        return [
            read_result_application(row)
            for row in self._conn.execute(
                "SELECT * FROM result_applications WHERE term = ? AND number = ? ORDER BY rowid",
                (term, number),
            )
        ]

    def list_government_review_applications(
        self, term: int, number: str
    ) -> list[ResultApplication]:
        if not number.startswith((RCL_PREFIX, WYKAZ_PREFIX)):
            return []
        cursor = self._conn.cursor()
        cursor.row_factory = sqlite3.Row
        try:
            applications: list[ResultApplication] = []
            for row in cursor.execute(
                "SELECT * FROM result_applications WHERE term=? AND number=? ORDER BY id",
                (term, number),
            ):
                application = read_result_application(row)
                if (
                    application.phase == "projection"
                    and application.generation is None
                    and application.target is not None
                    and application.target.kind == "document_review"
                ):
                    applications.append(application)
            return applications
        except ValueError, TypeError, IndexError, KeyError, sqlite3.Error, RecursionError:
            raise ValueError("invalid_government_review_application") from None
        finally:
            cursor.close()

    def save_review_work(self, term: int, number: str, work: tuple[ReviewWork, ...]) -> None:
        self._conn.execute(
            "UPDATE bills SET review_work_json = ? WHERE term = ? AND number = ?",
            (
                json.dumps([item.model_dump(mode="json") for item in work]) if work else None,
                term,
                number,
            ),
        )

    def save_committee_documents(
        self, term: int, number: str, sources: tuple[ReviewSource, ...]
    ) -> None:
        self._conn.execute(
            "UPDATE bills SET committee_documents_json = ? WHERE term = ? AND number = ?",
            (json.dumps([source.model_dump(mode="json") for source in sources]), term, number),
        )

    def save_document_review(self, term: int, number: str, review: DocumentReviewRecord) -> None:
        validate_secondary_dependency(self._conn, review, review.review.purpose)
        column = {
            "consultation_review": "consultation_review_json",
            "commission_review": "commission_review_json",
            "act_application": "act_application_review_json",
        }[review.review.purpose]
        self._conn.execute(
            f"UPDATE bills SET {column} = ? WHERE term = ? AND number = ?",
            (
                self._preserve_legacy_json(
                    review.model_dump_json(),
                    f"SELECT {column} FROM bills WHERE term = ? AND number = ?",
                    (term, number),
                ),
                term,
                number,
            ),
        )

    def list_review_work(self) -> list[Bill]:
        rows = self._conn.execute(
            "SELECT * FROM bills WHERE review_work_json IS NOT NULL ORDER BY term, number"
        ).fetchall()
        return [self._row_to_bill(row) for row in rows]

    def list_acts_for_review(self, channel_id: str) -> list[Bill]:
        rows = self._conn.execute(
            "SELECT b.* FROM bills b WHERE act_json IS NOT NULL AND status != 'linked'"
            " AND EXISTS (SELECT 1 FROM publications p WHERE p.term=b.term AND p.number=b.number"
            " AND p.channel_id=? AND p.kind='new_bill' AND p.status='sent')"
            " ORDER BY b.term, b.number",
            (channel_id,),
        ).fetchall()
        return [self._row_to_bill(row) for row in rows]

    def effective_rule_publications(
        self, term: int, number: str, channel_id: str
    ) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE term=? AND number=? AND channel_id=?"
            " AND kind='effective_rules' ORDER BY id",
            (term, number, channel_id),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def effective_rule_refs(self, term: int, number: str, channel_id: str) -> set[str]:
        return {
            row[0]
            for row in self._conn.execute(
                "SELECT rule_ref FROM effective_rule_links"
                " WHERE term=? AND number=? AND channel_id=?",
                (term, number, channel_id),
            )
        }

    def reserve_effective_rules(self, pub: Publication, refs: tuple[str, ...]) -> None:
        assert pub.id is not None
        self._conn.executemany(
            "INSERT INTO effective_rule_links VALUES (?, ?, ?, ?, ?)",
            [(pub.term, pub.number, pub.channel_id, ref, pub.id) for ref in refs],
        )

    def save_analysis_memo(
        self,
        key: str,
        record_json: str,
        *,
        term: int | None = None,
        number: str | None = None,
        used_at: datetime | None = None,
    ) -> None:
        with self.atomic():
            cursor = self._conn.cursor()
            cursor.row_factory = sqlite3.Row
            try:
                previous = cursor.execute(
                    "SELECT record_json, term, number FROM analysis_memo WHERE key=?", (key,)
                ).fetchone()
            finally:
                cursor.close()
            if previous is not None:
                validate_review_memo_row(
                    self._conn, previous["record_json"], previous["term"], previous["number"]
                )
            reader = PrimaryProjectionReader(self._conn)
            if previous is None:
                if term is not None and number is not None:
                    record_json = reader.compact(record_json, ProjectionOwner(term, number))
            else:
                record_json = previous["record_json"]
                old_owner = (previous["term"], previous["number"])
                new_owner = (
                    term if term is not None else previous["term"],
                    number if number is not None else previous["number"],
                )
                materialized = reader.materialize(
                    "record_json",
                    record_json,
                    ProjectionOwner(*old_owner) if None not in old_owner else None,
                )
                if old_owner != new_owner:
                    record_json = materialized
                elif None not in old_owner:
                    record_json = reader.compact(record_json, ProjectionOwner(*old_owner))
            self._conn.execute(
                "INSERT INTO analysis_memo(key, record_json, term, number, last_used_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                " record_json = excluded.record_json,"
                " term = COALESCE(excluded.term, analysis_memo.term),"
                " number = COALESCE(excluded.number, analysis_memo.number),"
                " last_used_at = COALESCE(excluded.last_used_at, analysis_memo.last_used_at)",
                (key, record_json, term, number, _iso(used_at)),
            )

    def save_update_delivery(self, change_id: int, channel_id: str, delivery: DeliveryPlan) -> None:
        original = self.get_update_publication(change_id, channel_id)
        if original is not None and source_context_blocked(self, original):
            return
        self._conn.execute(
            "UPDATE publications SET delivery_json = ?, attention_json = ?"
            " WHERE status_change_id = ?"
            " AND error IS NOT 'source_context_invalid'"
            " AND channel_id = ? AND kind = 'status_update' AND delivery_json IS NULL"
            " AND status != 'sent'",
            (
                delivery.model_dump_json(),
                delivery.attention.model_dump_json() if delivery.attention else None,
                change_id,
                channel_id,
            ),
        )

    def replace_unsent_update_delivery(
        self, change_id: int, channel_id: str, delivery: DeliveryPlan
    ) -> bool:
        original = self.get_update_publication(change_id, channel_id)
        if original is not None and source_context_blocked(self, original):
            return False
        cur = self._conn.execute(
            "UPDATE publications SET delivery_json = ?, attention_json = ?"
            " WHERE status_change_id = ?"
            " AND error IS NOT 'source_context_invalid'"
            " AND channel_id = ? AND kind = 'status_update' AND status = 'queued'"
            " AND attempts = 0"
            " AND NOT EXISTS (SELECT 1 FROM digest_event_links"
            " WHERE source_publication_id = publications.id)"
            " AND COALESCE(json_array_length(delivery_json, '$.update_group'), 0) = 0",
            (
                delivery.model_dump_json(),
                delivery.attention.model_dump_json() if delivery.attention else None,
                change_id,
                channel_id,
            ),
        )
        return cur.rowcount == 1

    def replace_unattempted_delivery(self, publication_id: int, delivery: DeliveryPlan) -> bool:
        original = self.publication_by_id(publication_id)
        if original is not None and source_context_blocked(self, original):
            return False
        if (
            original is not None
            and original.delivery is not None
            and self.digest_sources(publication_id)
            and (
                original.delivery.rendered_text != delivery.rendered_text
                or original.delivery.digest_event_ids != delivery.digest_event_ids
            )
        ):
            return False
        cur = self._conn.execute(
            "UPDATE publications SET delivery_json = ?, attention_json = ?"
            " WHERE id = ? AND status = 'queued'"
            " AND attempts = 0"
            " AND NOT EXISTS (SELECT 1 FROM digest_event_links"
            " WHERE source_publication_id = publications.id)"
            " AND COALESCE(json_array_length(delivery_json, '$.update_group'), 0) = 0",
            (
                delivery.model_dump_json(),
                delivery.attention.model_dump_json() if delivery.attention else None,
                publication_id,
            ),
        )
        return cur.rowcount == 1

    def freeze_direct_delivery(self, original: Publication, delivery: DeliveryPlan) -> bool:
        try:
            saved = direct_root_plan(original)
        except ValueError:
            return False
        assert original.id is not None, "direct_root_plan admits only stored reader roots"
        row = self._conn.execute(
            "SELECT * FROM publications WHERE id = ?", (original.id,)
        ).fetchone()
        if (
            row is None
            or self._row_to_publication(row) != original
            or saved.render_origin is not None
            or delivery.render_origin != "legacy_snapshot"
            or not delivery.rendered_text.strip()
            or utf16_length(delivery.rendered_text) > MESSAGE_LIMIT
            or self.digest_owner(original.id) is not None
            or self.digest_sources(original.id)
        ):
            return False
        updates: dict[str, object] = {
            "rendered_text": delivery.rendered_text,
            "render_origin": delivery.render_origin,
        }
        if original.kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL) or (
            original.kind is PublicationKind.STATUS_UPDATE and saved.edit_message_id is None
        ):
            if delivery.today is None:
                return False
            updates["today"] = delivery.today
        if delivery.reply_to != saved.reply_to:
            if original.kind is not PublicationKind.STATUS_UPDATE or saved.reply_to is not None:
                return False
            card = self.get_publication(
                original.term, original.number, PublicationKind.NEW_BILL, original.channel_id
            )
            if (
                card is None
                or card.status is not PublicationStatus.SENT
                or card.message_id is None
                or card.message_id <= 0
                or card.message_id != delivery.reply_to
                or saved.edit_message_id is not None
            ):
                return False
            updates["reply_to"] = delivery.reply_to
        if delivery.continuation_ids:
            if not self._physical_delivery_parts(original, delivery):
                return False
            updates.update(continuation_ids=delivery.continuation_ids, held_change_ids=())
        if delivery != saved.model_copy(update=updates):
            return False
        raw: dict[str, object] = json.loads(row["delivery_json"])
        encoded = delivery.model_dump(mode="json")
        raw.update({key: encoded[key] for key in updates})
        changed = self._conn.execute(
            "UPDATE publications SET delivery_json = ?"
            " WHERE id = ? AND status = ? AND attempts = ? AND delivery_json = ?",
            (
                json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                original.id,
                original.status.value,
                original.attempts,
                row["delivery_json"],
            ),
        )
        return changed.rowcount == 1

    def _physical_delivery_parts(self, original: Publication, delivery: DeliveryPlan) -> bool:
        assert original.delivery is not None, "physical roots retain their original saved plan"
        identities = delivery.continuation_ids
        if len(set(identities)) != len(identities) or delivery.held_change_ids:
            return False
        parent = original.id
        for index, identity in enumerate(identities, 2):
            child = self.publication_by_id(identity)
            if (
                child is None
                or child.kind is not PublicationKind.MESSAGE_PART
                or child.status is not PublicationStatus.QUEUED
                or child.attempts
                or child.message_id is not None
                or child.term != original.term
                or child.number != original.number
                or child.channel_id != original.channel_id
                or child.ref != f"message/{original.id}/{index}"
            ):
                return False
            plan = child.delivery
            held = original.delivery.held_change_ids if index == len(identities) + 1 else ()
            if (
                plan is None
                or not plan.rendered_text.strip()
                or utf16_length(plan.rendered_text) > MESSAGE_LIMIT
                or plan.continuation_parent != parent
                or plan.continuation_root != original.id
                or plan.continuation_index != index
                or plan.continuation_ids
                or plan.reply_to is not None
                or plan.edit_message_id is not None
                or plan.revision_of is not None
                or plan.destination != "direct"
                or plan.policy_version != "attention-v1"
                or plan.publication_ids
                or plan.digest_parts
                or plan.digest_event_ids
                or plan.held_change_ids != held
            ):
                return False
            parent = identity
        return True

    def freeze_sitting_delivery(
        self,
        members: tuple[Publication, ...],
        plans: tuple[DeliveryPlan, ...],
        *,
        content_format: tuple[str, str] | None = None,
    ) -> bool:
        identities = tuple(member.id for member in members)
        if not members or len(members) != len(plans) or len(set(identities)) != len(identities):
            return False
        if any(
            stored is not None and source_context_blocked(self, stored)
            for identity in identities
            if identity is not None
            for stored in (self.publication_by_id(identity),)
        ):
            return False
        prepared: list[tuple[str, int, str, int, str]] = []
        with self.atomic():
            for member, plan in zip(members, plans, strict=True):
                try:
                    saved = sitting_root_plan(member)
                except ValueError:
                    return False
                if member.channel_id != members[0].channel_id:
                    return False
                assert member.id is not None, "sitting_root_plan requires a stored identity"
                row = self._conn.execute(
                    "SELECT * FROM publications WHERE id = ?", (member.id,)
                ).fetchone()
                if (
                    row is None
                    or self._row_to_publication(row) != member
                    or self.digest_owner(member.id) is not None
                    or self.digest_sources(member.id)
                ):
                    return False
                updates: dict[str, object] = {
                    "render_origin": "legacy_snapshot"
                    if member.status is PublicationStatus.FAILED or member.attempts
                    else None
                }
                if plan.sitting_group:
                    if (
                        plan.sitting_group != identities
                        or not plan.group_text.strip()
                        or utf16_length(plan.group_text) > MESSAGE_LIMIT
                        or plan.group_text != plans[0].group_text
                    ):
                        return False
                    updates.update(sitting_group=identities, group_text=plan.group_text)
                else:
                    if (
                        len(members) != 1
                        or not plan.rendered_text.strip()
                        or utf16_length(plan.rendered_text) > MESSAGE_LIMIT
                    ):
                        return False
                    updates["rendered_text"] = plan.rendered_text
                    if plan.continuation_ids:
                        if not self._physical_delivery_parts(member, plan):
                            return False
                        updates.update(continuation_ids=plan.continuation_ids, held_change_ids=())
                if plan != saved.model_copy(update=updates):
                    return False
                raw: dict[str, object] = json.loads(row["delivery_json"])
                encoded = plan.model_dump(mode="json")
                raw.update({key: encoded[key] for key in updates})
                prepared.append(
                    (
                        json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                        member.id,
                        member.status.value,
                        member.attempts,
                        row["delivery_json"],
                    )
                )
            for parameters in prepared:
                changed = self._conn.execute(
                    "UPDATE publications SET delivery_json = ?"
                    " WHERE id = ? AND status = ? AND attempts = ? AND delivery_json = ?",
                    parameters,
                )
                if changed.rowcount != 1:
                    raise ValueError("Cannot freeze a changed sitting delivery")
            if content_format is not None:
                self._record_sitting_content(members, plans, content_format)
        return True

    def _record_sitting_content(
        self,
        members: tuple[Publication, ...],
        plans: tuple[DeliveryPlan, ...],
        content_format: tuple[str, str],
    ) -> None:
        if any(
            member.status is not PublicationStatus.QUEUED or member.attempts for member in members
        ):
            return
        root = members[0]
        assert root.id is not None, "the sitting freeze requires stored identities"
        language, formatter_version = content_format
        if (
            len(members) > MAX_SITTING_HISTORY_ROWS
            or len(plans[0].continuation_ids) >= MAX_SITTING_HISTORY_ROWS
        ):
            log.warning("sitting content history omitted: publication=%s reason=rows", root.id)
            return
        try:
            bills = {
                delivery.bill_json: ContentSnapshot(kind="bill", payload_json=delivery.bill_json)
                for delivery in plans
            }
            logical = tuple(
                SittingContentMember.model_validate(
                    {
                        "id": member.id,
                        "term": member.term,
                        "number": member.number,
                        "kind": member.kind.value,
                        "ref": member.ref,
                        "bill_snapshot_id": bills[delivery.bill_json].snapshot_id,
                        "plan": sitting_content_context(delivery),
                    }
                )
                for member, delivery in zip(members, plans, strict=True)
            )
            root_plan = plans[0]
            physical = [
                SittingContentPart(
                    id=root.id,
                    html=root_plan.group_text or root_plan.rendered_text,
                    disable_notification=root_plan.disable_notification,
                )
            ]
            for identity in root_plan.continuation_ids:
                child = self.publication_by_id(identity)
                assert child is not None and child.delivery is not None, (
                    "the sitting freeze validated the full continuation chain"
                )
                physical.append(
                    SittingContentPart(
                        id=identity,
                        html=child.delivery.rendered_text,
                        disable_notification=child.delivery.disable_notification,
                    )
                )
            offered = SittingContentPlan.model_construct(
                channel_id=root.channel_id, members=logical, parts=tuple(physical)
            )
            snapshot = ContentSnapshot(kind="plan", payload_json=offered.model_dump_json())
            if (
                len(snapshot.payload_json.encode())
                + sum(len(bill.payload_json.encode()) for bill in bills.values())
                > MAX_SITTING_HISTORY_BYTES
            ):
                packed = {
                    raw: replacement
                    for raw, bill in bills.items()
                    if (replacement := pack_original_bill_snapshot(bill)) is not None
                }
                if packed:
                    offered = offered.model_copy(
                        update={
                            "members": tuple(
                                member.model_copy(
                                    update={
                                        "bill_snapshot_id": packed[delivery.bill_json].snapshot_id,
                                        "packed_bill": True,
                                    }
                                )
                                if delivery.bill_json in packed
                                else member
                                for member, delivery in zip(logical, plans, strict=True)
                            )
                        }
                    )
                    bills.update(packed)
                    snapshot = ContentSnapshot(kind="plan", payload_json=offered.model_dump_json())
                if (
                    len(snapshot.payload_json.encode())
                    + sum(len(bill.payload_json.encode()) for bill in bills.values())
                    > MAX_SITTING_HISTORY_BYTES
                ):
                    log.warning(
                        "sitting content history omitted: publication=%s reason=bytes", root.id
                    )
                    return
            plan = SittingContentPlan.model_validate_json(snapshot.payload_json)
            version = ContentVersion(
                root_publication_id=root.id,
                reason="original",
                language=language,
                formatter_version=formatter_version,
                sitting_profile="sitting-content-v1",
                plan_snapshot_id=snapshot.snapshot_id,
                recorded_at=root.created_at,
            )
        except ValueError:
            log.warning("sitting content history omitted: publication=%s reason=profile", root.id)
            return
        for bill in bills.values():
            self.save_content_snapshot(bill)
        self.save_content_snapshot(snapshot)
        self.save_content_version(version)
        self._conn.executemany(
            "INSERT INTO publication_content(publication_id,content_id) VALUES(?,?)",
            [(identity, version.content_id) for identity in plan.bindings],
        )
        verify_sitting_content(self._conn, version, require_bound=True)

    def get_update_publication(self, change_id: int, channel_id: str) -> Publication | None:
        row = self._conn.execute(
            "SELECT * FROM publications WHERE status_change_id = ?"
            " AND channel_id = ? AND kind = 'status_update'",
            (change_id, channel_id),
        ).fetchone()
        return self._row_to_publication(row) if row is not None else None

    def list_amendment_revisions(self, publication_id: int) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE kind = 'amendments_revision'"
            " AND (json_extract(delivery_json, '$.revision_of') = ?"
            " OR delivery_json IS NULL AND EXISTS (SELECT 1 FROM publication_content binding"
            " JOIN content_versions version ON version.content_id=binding.content_id"
            " WHERE binding.publication_id=publications.id AND (version.root_publication_id=?"
            " OR json_extract(version.version_json, '$.root_publication_id')=?)))"
            " ORDER BY id DESC",
            (publication_id, publication_id, publication_id),
        ).fetchall()
        revisions = [self._row_to_publication(row) for row in rows]
        for revision in revisions:
            if revision.delivery is not None:
                continue
            assert revision.id is not None, "the repository reads saved revision rows"
            version = self.get_publication_content(revision.id)
            original = self.publication_by_id(publication_id)
            if (
                version is None
                or version.reason != "amendments_revision"
                or version.root_publication_id != publication_id
                or original is None
                or (revision.term, revision.number, revision.channel_id)
                != (original.term, original.number, original.channel_id)
            ):
                raise ValueError("retained revision binding must identify its original owner")
        return revisions

    def list_deferred_updates(self, channel_id: str) -> list[Publication]:
        """Only never-attempted, unowned individual updates can be absorbed by analysis."""
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE channel_id = ? AND kind = 'status_update'"
            " AND status = 'queued' AND attempts = 0"
            " AND json_extract(delivery_json, '$.absorbed_by') IS NULL"
            " AND COALESCE(json_extract(delivery_json, '$.destination'), 'direct') != 'digest_only'"
            " AND COALESCE(json_array_length(delivery_json, '$.update_group'), 0) = 0"
            " AND COALESCE(json_extract(delivery_json, '$.rendered_text'), '') = ''"
            " AND (json_extract(delivery_json, '$.wait_until') IS NOT NULL"
            " OR json_extract(delivery_json, '$.schedule.route') = 'daily_updates') ORDER BY id",
            (channel_id,),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def list_scheduled_deliveries(self, channel_id: str) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE channel_id = ?"
            " AND status IN ('queued', 'failed', 'pending', 'unknown')"
            " AND json_extract(delivery_json, '$.schedule') IS NOT NULL"
            " AND json_extract(delivery_json, '$.absorbed_by') IS NULL ORDER BY id",
            (channel_id,),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def list_open_deliveries(self, channel_id: str) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE channel_id = ?"
            " AND status IN ('queued', 'failed', 'pending', 'unknown')"
            " AND delivery_json IS NOT NULL"
            " AND json_extract(delivery_json, '$.absorbed_by') IS NULL ORDER BY id",
            (channel_id,),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def get_delivery_alert(self, channel_id: str) -> DeliveryAlert | None:
        row = self._conn.execute(
            "SELECT * FROM delivery_alerts WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return DeliveryAlert.model_validate(dict(row)) if row else None

    def save_delivery_alert(self, alert: DeliveryAlert) -> None:
        self._conn.execute(
            "INSERT INTO delivery_alerts(channel_id, fingerprint, notified_at) VALUES (?, ?, ?)"
            " ON CONFLICT(channel_id) DO UPDATE SET fingerprint = excluded.fingerprint,"
            " notified_at = excluded.notified_at",
            (alert.channel_id, alert.fingerprint, alert.notified_at.isoformat()),
        )

    def tag_update_group(self, publication_ids: tuple[int, ...], ref: str) -> None:
        if any(
            stored is not None and source_context_blocked(self, stored)
            for identity in publication_ids
            for stored in (self.publication_by_id(identity),)
        ):
            raise ValueError("source_context_invalid prevents changing its update group")
        self._conn.executemany(
            "UPDATE publications SET ref = ? WHERE id = ? AND kind = 'status_update'",
            [(ref, identity) for identity in publication_ids],
        )

    def get_act_update(self, term: int, number: str, channel_id: str) -> Publication | None:
        row = self._conn.execute(
            "SELECT * FROM publications WHERE term = ? AND number = ? AND channel_id = ?"
            " AND kind = 'status_update' AND json_extract(delivery_json, '$.with_act') = 1"
            " ORDER BY id LIMIT 1",
            (term, number, channel_id),
        ).fetchone()
        return self._row_to_publication(row) if row is not None else None

    def release_planned_changes(
        self, ids: tuple[int, ...], channel_id: str, *, message_id: int, sent_at: datetime
    ) -> None:
        self._conn.executemany(
            "UPDATE publications SET status = 'sent', message_id = ?, sent_at = ?"
            " WHERE status_change_id = ? AND channel_id = ?"
            " AND kind = 'status_update' AND (status = 'skipped' OR"
            " (status = 'queued' AND attempts = 0"
            " AND (json_extract(delivery_json, '$.wait_until') IS NOT NULL"
            " OR json_extract(delivery_json, '$.schedule.route') = 'daily_updates')))",
            [(message_id, sent_at.isoformat(), change_id, channel_id) for change_id in ids],
        )

    def record_analysis_failure(self, term: int, number: str, error: str) -> None:
        self._conn.execute(
            """
            UPDATE bills SET status = ?, analysis_attempts = analysis_attempts + 1, last_error = ?
            WHERE term = ? AND number = ?
            """,
            (BillStatus.ANALYSIS_FAILED.value, error[:2000], term, number),
        )

    def save_llm_batch(self, batch: LlmBatch, items: Sequence[LlmBatchItem]) -> None:
        with self.atomic():
            self._save_llm_batch_rows(batch, items)

    def save_amendment_intent(self, intent: BatchIntent, snapshot: AmendmentInputSnapshot) -> None:
        with self.atomic():
            existing = self._conn.execute(
                "SELECT 1 FROM llm_batch_intents WHERE custom_id=?", (intent.request.custom_id,)
            ).fetchone()
            if existing is not None:
                return
            if (
                not isinstance(intent.meta, DigestItemMeta)
                or intent.meta.amendment_input_ref is None
                or intent.meta.amendment_input_ref.snapshot_id != snapshot.snapshot_id
            ):
                raise ValueError("invalid_amendment_input")
            write_shared_amendment_input(self._conn, snapshot)
            self.save_batch_intent(intent)

    def save_batch_intent(self, intent: BatchIntent) -> None:
        intent.require_scan_binding()
        validate_scan_attachment(intent)
        validate_intent_input(self._conn, intent)
        validate_multipart_intent(self._conn, intent)
        validate_word_formula_intent(self._conn, intent)
        inserted = self._conn.execute(
            "INSERT OR IGNORE INTO llm_batch_intents"
            " (custom_id, provider, request_json, meta_json, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                intent.request.custom_id,
                intent.provider,
                serialize_intent_request(intent.request),
                serialize_intent_metadata(intent),
                intent.state,
                intent.created_at.isoformat(),
            ),
        )
        if inserted.rowcount == 1:
            log.debug(
                "local batch intent write",
                extra=log_extra(
                    **_batch_log_fields(
                        intent.request.term,
                        intent.request.number,
                        intent.request.custom_id,
                        intent.meta,
                        request_sha256=intent.request.payload_sha256,
                    )
                ),
            )

    def batch_job(self, custom_id: str) -> BatchJob | None:
        submissions = self._conn.execute(
            "SELECT COUNT(*) FROM llm_batch_items WHERE custom_id = ?", (custom_id,)
        ).fetchone()[0]
        intent = self._conn.execute(
            "SELECT state, created_at FROM llm_batch_intents WHERE custom_id = ?", (custom_id,)
        ).fetchone()
        if intent is not None:
            return BatchJob(
                state=intent["state"],
                since=datetime.fromisoformat(intent["created_at"]),
                submissions=submissions,
            )
        row = self._conn.execute(
            "SELECT i.consumed_at, b.status, "
            "COALESCE(json_extract(i.meta_json, '$.queued_at'), b.submitted_at) AS since "
            "FROM llm_batch_items i JOIN llm_batches b USING (batch_id) "
            "WHERE i.custom_id = ? ORDER BY b.submitted_at DESC, i.rowid DESC LIMIT 1",
            (custom_id,),
        ).fetchone()
        if row is None:
            return None
        return BatchJob(
            state="failed"
            if row["consumed_at"] is not None or row["status"] == "failed"
            else "open",
            since=datetime.fromisoformat(row["since"]),
            submissions=submissions,
        )

    def computation_owner_terms(self, number: str, kind: BatchKind) -> tuple[int, ...]:
        rows = self._conn.execute(
            "SELECT term FROM llm_batch_items WHERE number = ? AND call_kind = ?"
            " UNION SELECT json_extract(request_json, '$.term') FROM llm_batch_intents"
            " WHERE json_extract(request_json, '$.number') = ?"
            " AND COALESCE(json_extract(request_json, '$.question.kind'),"
            " json_extract(request_json, '$.call_kind')) = ?"
            " UNION SELECT term FROM computation_results WHERE number = ?"
            " AND json_extract(result_json, '$.purpose') = ? ORDER BY term DESC",
            (number, kind, number, kind, number, kind),
        )
        return tuple(int(row[0]) for row in rows)

    def context_rejected_batch_ids(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT custom_id FROM llm_batch_items "
            "WHERE json_extract(result_json, '$.context_exceeded') = 1"
        )
        return {row["custom_id"] for row in rows}

    def set_observation(
        self,
        term: int,
        number: str,
        mode: ObservationMode | None,
        basis: ObservationBasis | None,
    ) -> None:
        self._conn.execute(
            "UPDATE bills SET observation_mode = ?, observation_basis = ?"
            " WHERE term = ? AND number = ?",
            (
                mode.value if mode is not None else None,
                basis.value if basis is not None else None,
                term,
                number,
            ),
        )

    def record_check(
        self,
        term: int,
        number: str,
        aspect: CheckAspect,
        *,
        at: datetime,
        ok: bool,
        outage: bool = False,
    ) -> None:
        """A success resets the failures; an outage is not the bill's failure and counts none."""
        stamp = _utc_iso(at)
        self._conn.execute(
            """
            INSERT INTO source_checks
                (term, number, aspect, last_success_at, last_attempt_at, failures, last_outage_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (term, number, aspect) DO UPDATE SET
                last_success_at = COALESCE(excluded.last_success_at, last_success_at),
                last_attempt_at = excluded.last_attempt_at,
                failures = CASE WHEN excluded.last_success_at IS NOT NULL THEN 0
                                ELSE failures + excluded.failures END,
                last_outage_at = COALESCE(excluded.last_outage_at, last_outage_at)
            """,
            (
                term,
                number,
                aspect.value,
                stamp if ok else None,
                stamp,
                0 if ok or outage else 1,
                stamp if outage and not ok else None,
            ),
        )

    def list_observation_candidates(self, channel_id: str) -> list[tuple[Bill, bool]]:
        """Live rows with an analysis or a card, each with whether its card was sent."""
        rows = self._conn.execute(
            """
            SELECT b.*, EXISTS (
                SELECT 1 FROM publications p WHERE p.term = b.term AND p.number = b.number
                AND p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?) AS has_card
            FROM bills b
            WHERE b.status != ? AND b.discontinued_at IS NULL
              AND (b.analysis_json IS NOT NULL OR EXISTS (
                SELECT 1 FROM publications p WHERE p.term = b.term AND p.number = b.number
                AND p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?))
            ORDER BY b.term, b.number
            """,
            (channel_id, BillStatus.LINKED.value, channel_id),
        ).fetchall()
        return [(self._row_to_bill(row), bool(row["has_card"])) for row in rows]

    def source_checks(self, term: int, number: str) -> dict[CheckAspect, SourceCheck]:
        rows = self._conn.execute(
            "SELECT * FROM source_checks WHERE term = ? AND number = ?", (term, number)
        ).fetchall()
        return {
            CheckAspect(row["aspect"]): SourceCheck(
                aspect=CheckAspect(row["aspect"]),
                last_success_at=_parse_dt(row["last_success_at"]),
                last_attempt_at=_parse_dt(row["last_attempt_at"]),
                failures=int(row["failures"]),
                last_outage_at=_parse_dt(row["last_outage_at"]),
            )
            for row in rows
        }

    def set_awaiting_batch(self, term: int, number: str, since: datetime | None) -> None:
        self._conn.execute(
            "UPDATE bills SET awaiting_batch_since = ? WHERE term = ? AND number = ?",
            (_utc_iso(since) if since is not None else None, term, number),
        )

    def list_queued_batch_intents(self) -> list[BatchIntent]:
        return self._batch_intents("queued")

    def list_submitting_batch_intents(self) -> list[BatchIntent]:
        return self._batch_intents("submitting")

    def _batch_intents(self, state: str) -> list[BatchIntent]:
        rows = self._conn.execute(
            "SELECT * FROM llm_batch_intents WHERE state = ? ORDER BY created_at, custom_id",
            (state,),
        ).fetchall()
        intents = [
            BatchIntent(
                request=(request := parse_batch_request(row["request_json"])),
                meta=intent_metadata(request, row["meta_json"]),
                provider=row["provider"],
                state=row["state"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

        for intent in intents:
            validate_scan_attachment(intent)
            validate_intent_input(self._conn, intent)
            validate_word_formula_intent(self._conn, intent)
        return intents

    def mark_batch_intents_submitting(self, custom_ids: Sequence[str]) -> None:
        self._conn.executemany(
            "UPDATE llm_batch_intents SET state = 'submitting'"
            " WHERE custom_id = ? AND state = 'queued'",
            [(custom_id,) for custom_id in custom_ids],
        )

    def mark_batch_intents_queued(self, custom_ids: Sequence[str]) -> None:
        self._conn.executemany(
            "UPDATE llm_batch_intents SET state = 'queued'"
            " WHERE custom_id = ? AND state = 'submitting'",
            [(custom_id,) for custom_id in custom_ids],
        )

    def delete_batch_intents(self, custom_ids: Sequence[str]) -> None:
        self._conn.executemany(
            "DELETE FROM llm_batch_intents WHERE custom_id = ?",
            [(custom_id,) for custom_id in custom_ids],
        )

    def _save_llm_batch_rows(self, batch: LlmBatch, items: Sequence[LlmBatchItem]) -> None:
        for item in items:
            manifest = item.meta.frozen_request_manifest
            if manifest is not None:
                manifest.require_request(item.call_kind, item.meta.request_sha256)
                if manifest.provider != batch.provider:
                    raise ValueError("Frozen request manifest differs from its batch provider")
            validate_item_input(self._conn, item)
            validate_multipart_item(self._conn, item)
        self._conn.execute(
            """
            INSERT INTO llm_batches
                (batch_id, provider, call_kind, submitted_at, status, request_count,
                 estimated_cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch.batch_id,
                batch.provider,
                batch.call_kind,
                _iso(batch.submitted_at),
                batch.status,
                batch.request_count,
                batch.estimated_cost_usd,
            ),
        )
        self._conn.executemany(
            """
            INSERT INTO llm_batch_items (batch_id, custom_id, call_kind, term, number, meta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    i.batch_id,
                    i.custom_id,
                    i.call_kind,
                    i.term,
                    i.number,
                    self._submitted_item_metadata(batch.provider, i),
                )
                for i in items
            ],
        )
        for item in items:
            log.debug(
                "local batch item write",
                extra=log_extra(
                    **_batch_log_fields(
                        item.term, item.number, item.custom_id, item.meta, batch_id=item.batch_id
                    )
                ),
            )

    def _submitted_item_metadata(self, provider: BatchProvider, item: LlmBatchItem) -> str:
        meta = item.meta
        if meta.request_sha256 is None:
            return meta.model_dump_json()
        row = self._conn.execute(
            "SELECT request_json, meta_json FROM llm_batch_intents"
            " WHERE custom_id=? AND provider=? AND state='submitting'",
            (item.custom_id, provider),
        ).fetchone()
        if row is None:
            return meta.model_dump_json()
        try:
            request = parse_batch_request(row["request_json"])
        except (ValueError, RecursionError, KeyError) as exc:
            if meta.frozen_request_manifest is not None:
                raise ValueError(
                    "Frozen request manifest has an invalid submitting intent"
                ) from None
            if isinstance(exc, KeyError):
                raise
            return meta.model_dump_json()
        if (
            request.custom_id,
            request.term,
            request.number,
            request.call_kind,
            request.payload_sha256,
        ) != (item.custom_id, item.term, item.number, item.call_kind, meta.request_sha256):
            if meta.frozen_request_manifest is not None:
                raise ValueError("Frozen request manifest differs from its submitting intent")
            return meta.model_dump_json()
        try:
            manifest = capture_frozen_request_manifest(request, provider)
        except ValueError, RecursionError:
            if meta.frozen_request_manifest is not None:
                raise
            return meta.model_dump_json()
        if meta.frozen_request_manifest is not None:
            if meta.frozen_request_manifest != manifest:
                raise ValueError("Frozen request manifest differs from its submitting intent")
            if item.call_kind not in ("consultation_review", "supplement"):
                return meta.model_dump_json()
        if manifest is None:
            return meta.model_dump_json()
        if isinstance(meta, DigestItemMeta) and item.call_kind == "consultation_review":
            review_witness = None
            with suppress(ValueError, TypeError, RecursionError, KeyError):
                review_witness = capture_submitted_review_input(
                    request, manifest, intent_metadata(request, row["meta_json"]), meta
                )
            if review_witness is not None or meta.review_input_witness is not None:
                meta = meta.model_copy(update={"review_input_witness": review_witness})
        if (
            isinstance(meta, DigestItemMeta)
            and item.call_kind == "supplement"
            and meta.scan_input is not None
        ):
            supplement_witness = None
            with suppress(ValueError, TypeError, RecursionError, KeyError):
                supplement_witness = capture_submitted_supplement_scan_input(
                    request, manifest, item, intent_metadata(request, row["meta_json"])
                )
            if supplement_witness is not None or meta.supplement_scan_input is not None:
                meta = meta.model_copy(update={"supplement_scan_input": supplement_witness})
        elif isinstance(meta, DigestItemMeta) and meta.scan_input is not None:
            captured = None
            with suppress(ValueError, TypeError, RecursionError):
                captured = capture_submitted_amendment_scan_input(
                    request, item, intent_metadata(request, row["meta_json"])
                )
            if captured is not None:
                witness, context_json = captured
                if context_json is not None:
                    write_amendment_scan_context(self._conn, witness, context_json)
                meta = meta.model_copy(update={"amendment_scan_input": witness})
        return meta.model_copy(update={"frozen_request_manifest": manifest}).model_dump_json()

    def list_open_llm_batches(self) -> list[LlmBatch]:
        rows = self._conn.execute(
            "SELECT * FROM llm_batches WHERE EXISTS (SELECT 1 FROM llm_batch_items i"
            "             WHERE i.batch_id = llm_batches.batch_id AND i.consumed_at IS NULL)"
            " ORDER BY submitted_at"
        ).fetchall()
        return [_row_to_llm_batch(r) for r in rows]

    def mark_llm_batch_polled(
        self, batch_id: str, *, status: BatchStatus, polled_at: datetime
    ) -> None:
        self._conn.execute(
            "UPDATE llm_batches SET status = ?, polled_at = ? WHERE batch_id = ?",
            (status, _iso(polled_at), batch_id),
        )

    def mark_llm_batch_collected(self, batch_id: str, *, completed_at: datetime) -> None:
        self._conn.execute(
            "UPDATE llm_batches SET completed_at = ? WHERE batch_id = ?",
            (_iso(completed_at), batch_id),
        )

    def list_unforgotten_llm_batches(self) -> list[LlmBatch]:
        rows = self._conn.execute(
            "SELECT * FROM llm_batches WHERE completed_at IS NOT NULL AND forgotten_at IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM llm_batch_items i"
            "                 WHERE i.batch_id = llm_batches.batch_id AND i.consumed_at IS NULL)"
            " ORDER BY submitted_at"
        ).fetchall()
        return [_row_to_llm_batch(r) for r in rows]

    def mark_llm_batch_forgotten(self, batch_id: str, *, forgotten_at: datetime) -> None:
        self._conn.execute(
            "UPDATE llm_batches SET forgotten_at = ? WHERE batch_id = ?",
            (_iso(forgotten_at), batch_id),
        )

    def list_llm_batch_items(self, batch_id: str) -> list[LlmBatchItem]:
        rows = self._conn.execute(
            "SELECT * FROM llm_batch_items WHERE batch_id = ? AND consumed_at IS NULL"
            " ORDER BY custom_id",
            (batch_id,),
        ).fetchall()
        return [_row_to_llm_batch_item(r, self._conn) for r in rows]

    def get_consumed_primary_input(
        self, provider: BatchProvider, batch_id: str, custom_id: str, generation: int
    ) -> LlmBatchItem | None:
        return ContentSourceRows(self._conn).get_consumed_primary_input(
            provider, batch_id, custom_id, generation
        )

    def mark_llm_batch_item_consumed(
        self,
        batch_id: str,
        custom_id: str,
        *,
        consumed_at: datetime,
        result: BatchResult | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE llm_batch_items SET consumed_at = ?, result_json = ?"
            " WHERE batch_id = ? AND custom_id = ? AND consumed_at IS NULL",
            (_iso(consumed_at), result.model_dump_json() if result else None, batch_id, custom_id),
        )

    def list_unaccounted_batch_items(self) -> list[LlmBatchItem]:
        return [
            _row_to_llm_batch_item(row, self._conn)
            for row in self._conn.execute(
                "SELECT * FROM llm_batch_items WHERE result_json IS NOT NULL"
                " AND accounted_at IS NULL ORDER BY consumed_at, batch_id, custom_id"
            ).fetchall()
        ]

    def mark_batch_item_accounted(self, batch_id: str, custom_id: str, *, at: datetime) -> None:
        self._conn.execute(
            "UPDATE llm_batch_items SET accounted_at = ? WHERE batch_id = ? AND custom_id = ?",
            (_iso(at), batch_id, custom_id),
        )

    def reset_bill(
        self, term: int, number: str, status: BillStatus, *, reason: str | None = None
    ) -> None:
        """Operator action: put a bill back into `status` with a clean retry budget. `reason`
        replaces the last error (a bill silenced by `/skip` says so instead of keeping the
        prefilter's silence, which reads as a keyword miss)."""
        self._conn.execute(
            "UPDATE bills SET status = ?, analysis_attempts = 0, last_error = ?,"
            " analysis_generation = analysis_generation + 1, ready_analysis_json = NULL"
            " WHERE term = ? AND number = ?",
            (status.value, reason, term, number),
        )

    def list_by_status(
        self,
        statuses: list[BillStatus],
        *,
        limit: int,
        max_attempts: int | None = None,
    ) -> list[Bill]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        attempts_clause = "AND analysis_attempts < ?" if max_attempts is not None else ""
        params: list[object] = [s.value for s in statuses]
        if max_attempts is not None:
            params.append(max_attempts)
        params.append(limit)
        rows = self._conn.execute(
            f"""
            SELECT * FROM bills WHERE status IN ({placeholders}) {attempts_clause}
              AND discontinued_at IS NULL
            ORDER BY change_date DESC LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def list_rcl_missing_consultation(self, *, limit: int) -> list[Bill]:
        """RCL rows analysed or waiting to be, still missing their consultation window.

        `rcl_json IS NOT NULL` is not redundant: `json_extract(NULL, …)` is NULL too, so a bare
        `consultation IS NULL` matches every non-RCL bill as well.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM bills
            WHERE status IN (?, ?) AND discontinued_at IS NULL
              AND rcl_json IS NOT NULL
              AND json_extract(rcl_json, '$.consultation') IS NULL
            ORDER BY change_date ASC LIMIT ?
            """,
            (BillStatus.ANALYSIS_PENDING.value, BillStatus.ANALYZED.value, limit),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def list_skipped_with_joint_prints(self) -> list[Bill]:
        """Rows a prefilter skipped that name prints considered jointly with them.

        The group comes from the `/processes` listing itself (`printsConsideredJointly` is on 117
        of the 1,665 rows of term 10), so a row the keywords dropped before its text was ever
        read still knows it has one. `json_array_length` of a missing key is NULL, which the
        comparison filters out along with the empty arrays.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM bills
            WHERE status IN (?, ?) AND discontinued_at IS NULL
              AND json_array_length(
                      json_extract(summary_json, '$.prints_considered_jointly')
                  ) > 0
            ORDER BY term, number
            """,
            (BillStatus.SKIPPED_PREFILTER.value, BillStatus.SKIPPED_TEXT_PREFILTER.value),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def count_document_work(self) -> dict[str, int]:
        return dict(
            self._conn.execute(
                "SELECT state, COUNT(*) FROM document_work WHERE state != 'complete' GROUP BY state"
            )
        )

    def count_by_status(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, count(*) FROM bills WHERE discontinued_at IS NULL GROUP BY status"
        ).fetchall()
        return {str(status): int(count) for status, count in rows}

    def count_publications(self, channel_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, count(*) FROM publications WHERE channel_id = ? GROUP BY status",
            (channel_id,),
        ).fetchall()
        return {str(status): int(count) for status, count in rows}

    def list_failing_checks(
        self, *, min_failures: int, limit: int
    ) -> list[tuple[Bill, SourceCheck]]:
        rows = self._conn.execute(
            """
            SELECT b.*, c.aspect AS check_aspect, c.last_success_at AS check_success,
                   c.last_attempt_at AS check_attempt, c.failures AS check_failures,
                   c.last_outage_at AS check_outage
            FROM source_checks c JOIN bills b ON b.term = c.term AND b.number = c.number
            WHERE c.failures >= ? AND b.status != ? AND b.discontinued_at IS NULL
            ORDER BY c.failures DESC, b.term, b.number, c.aspect LIMIT ?
            """,
            (min_failures, BillStatus.LINKED.value, limit),
        ).fetchall()
        return [
            (
                self._row_to_bill(row),
                SourceCheck(
                    aspect=CheckAspect(row["check_aspect"]),
                    last_success_at=_parse_dt(row["check_success"]),
                    last_attempt_at=_parse_dt(row["check_attempt"]),
                    failures=int(row["check_failures"]),
                    last_outage_at=_parse_dt(row["check_outage"]),
                ),
            )
            for row in rows
        ]

    def list_stuck_publications(self, channel_id: str, *, limit: int) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE channel_id = ? AND status IN ('pending', 'unknown')"
            " ORDER BY created_at LIMIT ?",
            (channel_id, limit),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def search(self, text: str, *, limit: int) -> list[Bill]:
        """A title or number search for the operator, who remembers what a bill is about and
        not its number. SQLite folds case for ASCII only, so a Polish letter matches as it was
        typed; the titles are mixed-case Polish and a word from the middle is the usual query.
        """
        rows = self._conn.execute(
            "SELECT * FROM bills ORDER BY change_date DESC, term, number",
        ).fetchall()
        query = text.strip().casefold()
        matches: list[Bill] = []
        for row in rows:
            bill = self._row_to_bill(row)
            names = [bill.summary.title, bill.number, bill.reader_name]
            names.extend(name.name for name in bill.editorial.previous)
            if any(query in name.casefold() for name in names):
                matches.append(bill)
                if len(matches) >= limit:
                    break
        return matches

    def list_publish_candidates(
        self, channel_id: str, *, min_score: int, limit: int, max_attempts: int = 3
    ) -> list[Bill]:
        """Bills that still need a card. A card or an "alternative bill" reply settles the bill;
        a failed one leaves it listed until the attempts are used up.

        `min_score` is the bar for a card, which is a message in the feed of every reader. The
        reply a jointly considered print gets is a message in a thread they already follow, so it
        is asked for separately (`list_joint_reply_candidates`) and only relevance decides it.
        """
        rows = self._conn.execute(
            f"""
            SELECT b.* FROM bills b
            WHERE b.status = ? AND b.analysis_json IS NOT NULL
              AND b.discontinued_at IS NULL
              AND {_NO_CARD_YET}
            """,
            (BillStatus.ANALYZED.value, channel_id, max_attempts),
        ).fetchall()
        bills = [self._row_to_bill(r) for r in rows]
        eligible = [
            b
            for b in bills
            if b.analysis is not None
            and b.analysis.analysis.relevant
            and b.analysis.analysis.score >= min_score
        ]
        eligible.sort(
            key=lambda b: (
                -(b.analysis.analysis.score if b.analysis else 0),
                b.summary.document_date or b.summary.change_date.date(),
            )
        )
        return eligible[:limit]

    def list_joint_reply_candidates(
        self, channel_id: str, *, limit: int, max_attempts: int = 3
    ) -> list[Bill]:
        """Analysed, relevant bills that name prints considered jointly with them and have no
        post yet, whatever their own score.

        Whether any of them is really a reply is `services.joint.primary_of`'s answer and the
        publisher's to ask: this only widens what the publisher may see. The score is left out
        because the print has already been read and judged — dropping the answer under the bar
        would mean paying for a reading and throwing it away, and the reply goes into a thread
        its readers chose, not into everyone's feed.
        """
        rows = self._conn.execute(
            f"""
            SELECT b.* FROM bills b
            WHERE b.status = ? AND b.analysis_json IS NOT NULL
              AND b.discontinued_at IS NULL
              AND json_array_length(
                      json_extract(b.summary_json, '$.prints_considered_jointly')
                  ) > 0
              AND {_NO_CARD_YET}
            ORDER BY b.term, b.number
            LIMIT ?
            """,
            (BillStatus.ANALYZED.value, channel_id, max_attempts, limit),
        ).fetchall()
        bills = [self._row_to_bill(r) for r in rows]
        return [b for b in bills if b.analysis is not None and b.analysis.analysis.relevant]

    def list_tracked(
        self,
        channel_id: str,
        *,
        closed_grace_days: int,
        pending_decision_max_days: int,
        now: datetime,
        changed_since: datetime | None = None,
        unthreaded: bool = False,
    ) -> list[Bill]:
        """Published bills still worth polling, whichever term they belong to.

        `unthreaded` lists instead the bills observed for the website without a sent card, under
        the same windows; their changes are recorded and never delivered. A jointly considered
        print is left out: its group is followed through the card's process, and publishing owns
        its batch marker while the reply waits.

        Both windows count from `closure_date`, which the Sejm sets at the third reading, long
        before the Senate, the President or a veto vote begin — hence the second of them. A law
        the Sejm has passed has an act ahead of it and nothing but the act ends that wait, so it
        is followed for `pending_decision_max_days` however quiet it goes: druk 210 of term 9 was
        answered by the Sejm 182 days after its closure, and a 180-day cap dropped it two days
        before its own override, signature and act. The cost of the long window is one bill of
        term 10. A bill whose act is in Dziennik Ustaw is kept whatever its age until the act
        applies: a vacatio legis outruns `closed_grace_days`, and that is the one stretch with a
        fixed date to give. An entry into force the ELI API has not indexed yet keeps the bill for
        the same reason.

        `changed_since` narrows to bills whose `change_date` is that recent, plus bills passed and
        still waiting for their act: an ELI can appear with no visible change of the process.
        """
        cutoff = (now - timedelta(days=closed_grace_days)).date().isoformat()
        decision_cutoff = (now - timedelta(days=pending_decision_max_days)).date().isoformat()
        followed = (
            """
            SELECT b.* FROM bills b
            WHERE b.observation_mode IN ('full', 'metadata')
              AND COALESCE(json_array_length(b.summary_json, '$.prints_considered_jointly'), 0) = 0
              AND NOT EXISTS (
                SELECT 1 FROM publications p WHERE p.term = b.term AND p.number = b.number
                AND p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?)
            """
            if unthreaded
            else """
            SELECT b.* FROM bills b
            JOIN publications p ON p.term = b.term AND p.number = b.number
            WHERE p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?
            """
        )
        sql = f"""{followed}
              AND b.status != ? AND b.discontinued_at IS NULL
              AND (b.awaiting_batch_since IS NOT NULL
                   OR b.closure_date IS NULL OR b.closure_date >= ?
                   OR ((b.passed = 1 OR b.passed IS NULL) AND b.act_json IS NULL
                       AND b.closure_date >= ?)
                   OR (b.act_json IS NOT NULL
                       AND (b.entry_into_force IS NULL OR b.entry_into_force > ?))
                   OR (b.act_json IS NULL AND b.closure_date >= ?
                       AND {self._AWAITS_DECISION})
                   OR (b.act_json IS NULL AND {self._AWAITS_TRIBUNAL_OUTCOME}))
            """
        params: list[object] = [
            channel_id,
            BillStatus.LINKED.value,
            cutoff,
            decision_cutoff,
            now.date().isoformat(),
            (now - timedelta(days=pending_decision_max_days)).date().isoformat(),
        ]
        if changed_since is not None:
            # Timestamps are stored as ISO text in UTC; compare to the second. A wykaz row is
            # exempt: its `change_date` is the register's `Data publikacji`, which never moves,
            # and the whole register is downloaded every run anyway.
            sql += """
              AND (substr(b.change_date, 1, 19) >= substr(?, 1, 19)
                   OR (b.passed = 1 AND b.act_json IS NULL)
                   OR b.awaiting_batch_since IS NOT NULL
                   OR b.status = ?
                   OR b.number LIKE ?)
            """
            since = changed_since if changed_since.tzinfo is None else changed_since.astimezone(UTC)
            params.extend(
                [
                    since.replace(tzinfo=None).isoformat(),
                    BillStatus.REANALYSIS_READY.value,
                    f"{WYKAZ_PREFIX}%",
                ]
            )
        rows = self._conn.execute(sql + " ORDER BY b.term, b.number", params).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def save_authors(self, term: int, number: str, authors: BillAuthors) -> None:
        self._conn.execute(
            "UPDATE bills SET authors_json = ? WHERE term = ? AND number = ?",
            (authors.model_dump_json(), term, number),
        )

    def save_agenda(self, term: int, number: str, items: tuple[AgendaItem, ...]) -> None:
        payload = json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False)
        self._conn.execute(
            "UPDATE bills SET agenda_json = ? WHERE term = ? AND number = ?",
            (payload if items else None, term, number),
        )

    def list_awaiting_consultation_results(self, channel_id: str) -> list[Bill]:
        rows = self._conn.execute(
            """
            SELECT b.* FROM bills b
            JOIN publications p ON p.term = b.term AND p.number = b.number
            WHERE p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?
              AND b.status != ? AND b.discontinued_at IS NULL AND b.submission_json IS NOT NULL
              AND json_extract(b.submission_json, '$.public_consultation')
              AND NOT json_extract(b.submission_json, '$.consultation_results')
            ORDER BY b.term, b.number
            """,
            (channel_id, BillStatus.LINKED.value),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def save_senate(self, term: int, number: str, act: SenateAct) -> None:
        self._conn.execute(
            "UPDATE bills SET senate_json = ? WHERE term = ? AND number = ?",
            (act.model_dump_json(), term, number),
        )

    def save_act(self, term: int, number: str, act: ActInfo) -> None:
        self._conn.execute(
            "UPDATE bills SET act_json = ?, entry_into_force = ? WHERE term = ? AND number = ?",
            (act.model_dump_json(), _iso_date(act.entry_into_force), term, number),
        )

    # The last day of the public consultation, whoever runs it: the Sejm (/bills entry) or the
    # government (RCL consultation letter). NULL when the bill has none.
    _CONSULTATION_END = """
        COALESCE(
            CASE WHEN json_extract(b.submission_json, '$.public_consultation')
                 THEN json_extract(b.submission_json, '$.consultation_end') END,
            json_extract(b.rcl_json, '$.consultation.deadline')
        )
    """
    # A veto or referral for review remains actionable while the statutory decision window is open.
    _AWAITS_DECISION = """
        EXISTS (
            SELECT 1 FROM json_tree(b.stages_json)
            WHERE json_tree.key = 'stage_type'
              AND json_tree.value IN ('Veto', 'PresidentToTribunal')
        )
    """
    # A ruling can lead to signature, a fresh print, or promulgation; no legal deadline bounds it.
    _AWAITS_TRIBUNAL_OUTCOME = """
        EXISTS (
            SELECT 1 FROM json_tree(b.stages_json)
            WHERE json_tree.key = 'stage_type'
              AND json_tree.value = 'ConstitutionalTribunalRuling'
        )
    """
    # A one-off post blocks its bill once it is sent, skipped, pending or unknown; a failed one
    # leaves the bill listed so the poster can retry it within the attempt budget.
    _NO_SETTLED_POST = """
        AND NOT EXISTS (
            SELECT 1 FROM publications r
            WHERE r.term = b.term AND r.number = b.number AND r.kind = ? AND r.channel_id = ?
              AND r.status IN ('sent', 'skipped', 'pending', 'unknown', 'dismissed')
        )
    """

    def list_due_in_force(self, channel_id: str, *, today: date) -> list[Bill]:
        """Published bills whose act enters into force today or earlier, not yet reminded."""
        rows = self._conn.execute(
            f"""
            SELECT b.* FROM bills b
            JOIN publications p ON p.term = b.term AND p.number = b.number
            WHERE p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?
              AND b.entry_into_force IS NOT NULL AND b.entry_into_force <= ?
              AND b.status != 'linked' AND b.discontinued_at IS NULL
              {self._NO_SETTLED_POST}
            ORDER BY b.entry_into_force, b.number
            """,
            (channel_id, today.isoformat(), PublicationKind.IN_FORCE.value, channel_id),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def list_due_consultations(
        self, channel_id: str, *, today: date, days_before: int
    ) -> list[Bill]:
        """Published bills whose public consultation ends within `days_before` days (today
        included) and that have not been reminded yet."""
        last_day = (today + timedelta(days=days_before)).isoformat()
        rows = self._conn.execute(
            f"""
            SELECT b.* FROM bills b
            JOIN publications p ON p.term = b.term AND p.number = b.number
            WHERE p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?
              AND b.status != ? AND b.discontinued_at IS NULL
              AND {self._CONSULTATION_END} BETWEEN ? AND ?
              {self._NO_SETTLED_POST}
            ORDER BY {self._CONSULTATION_END}, b.number
            """,
            (
                channel_id,
                BillStatus.LINKED.value,
                today.isoformat(),
                last_day,
                PublicationKind.CONSULTATION_DEADLINE.value,
                channel_id,
            ),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def save_submission(self, term: int, number: str, submission: BillSubmission) -> None:
        self._conn.execute(
            "UPDATE bills SET submission_json = ? WHERE term = ? AND number = ?",
            (submission.model_dump_json(), term, number),
        )

    def list_pre_print(self) -> list[Bill]:
        rows = self._conn.execute(
            "SELECT * FROM bills WHERE number LIKE ? AND status != ?"
            " AND discontinued_at IS NULL ORDER BY change_date",
            (f"{PRE_PRINT_PREFIX}%", BillStatus.LINKED.value),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def list_rcl_entries_awaiting_print(self) -> list[Bill]:
        rows = self._conn.execute(
            "SELECT * FROM bills WHERE number LIKE ? AND status != ?"
            " AND discontinued_at IS NULL AND submission_json IS NOT NULL"
            " AND json_extract(rcl_json, '$.print_number') IS NULL ORDER BY change_date",
            (f"{RCL_PREFIX}%", BillStatus.LINKED.value),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def save_rcl(self, term: int, number: str, project: RclProject) -> None:
        self._conn.execute(
            "UPDATE bills SET rcl_json = ? WHERE term = ? AND number = ?",
            (storage_json(project), term, number),
        )

    def list_rcl_awaiting_link(self) -> list[Bill]:
        rows = self._conn.execute(
            "SELECT * FROM bills WHERE number LIKE ? AND status != ?"
            " AND json_extract(rcl_json, '$.print_number') IS NOT NULL ORDER BY term, number",
            (f"{RCL_PREFIX}%", BillStatus.LINKED.value),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def rcl_thread_awaits_druk(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM bills WHERE number LIKE ? AND status != ? AND observation_basis = ?"
            " AND discontinued_at IS NULL AND json_extract(rcl_json, '$.print_number') IS NULL"
            " LIMIT 1",
            (f"{RCL_PREFIX}%", BillStatus.LINKED.value, ObservationBasis.TELEGRAM_THREAD.value),
        ).fetchone()
        return row is not None

    def find_rcl(self, number: str) -> Bill | None:
        # RCL ids never repeat, and a project row lives in one term at a time (see
        # `move_rcl_projects`), so the number alone identifies the row.
        row = self._conn.execute(
            "SELECT * FROM bills WHERE number = ? AND number LIKE ? ORDER BY term DESC LIMIT 1",
            (number, f"{RCL_PREFIX}%"),
        ).fetchone()
        return self._row_to_bill(row) if row else None

    def save_seen_supplements(self, term: int, number: str, numbers: tuple[str, ...]) -> None:
        self._conn.execute(
            "UPDATE bills SET supplements_json = ? WHERE term = ? AND number = ?",
            (json.dumps(list(numbers), ensure_ascii=False), term, number),
        )

    def save_joint_comparison(self, term: int, number: str, record: JointRecord) -> None:
        validate_secondary_dependency(self._conn, record, "joint")
        self._conn.execute(
            "UPDATE bills SET joint_json = ? WHERE term = ? AND number = ?",
            (
                self._preserve_legacy_json(
                    record.model_dump_json(),
                    "SELECT joint_json FROM bills WHERE term = ? AND number = ?",
                    (term, number),
                ),
                term,
                number,
            ),
        )

    def save_wykaz(self, term: int, number: str, entry: WykazEntry) -> None:
        self._conn.execute(
            "UPDATE bills SET wykaz_json = ? WHERE term = ? AND number = ?",
            (entry.model_dump_json(), term, number),
        )

    def list_wykaz_awaiting_link(self) -> list[Bill]:
        rows = self._conn.execute(
            "SELECT * FROM bills WHERE number LIKE ? AND status != ?"
            " AND json_extract(wykaz_json, '$.rcl_project_id') IS NOT NULL ORDER BY term, number",
            (f"{WYKAZ_PREFIX}%", BillStatus.LINKED.value),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def find_wykaz(self, number: str) -> Bill | None:
        # A wykaz number identifies the entry on its own (it is the row's number), and the row
        # lives in one term at a time, as an RCL project does.
        row = self._conn.execute(
            "SELECT * FROM bills WHERE number = ? AND number LIKE ? ORDER BY term DESC LIMIT 1",
            (number, f"{WYKAZ_PREFIX}%"),
        ).fetchone()
        return self._row_to_bill(row) if row else None

    def find_by_rm_number(self, rm_number: str) -> Bill | None:
        return self._find_rcl("$.rm_number", rm_number)

    def find_by_wykaz_number(self, wykaz_number: str) -> Bill | None:
        return self._find_rcl("$.wykaz_number", wykaz_number)

    def find_print_by_rcl_num(self, term: int, rm_number: str) -> Bill | None:
        row = self._conn.execute(
            "SELECT * FROM bills WHERE term = ? AND number NOT LIKE ? AND number NOT LIKE ?"
            " AND number NOT LIKE ?"
            " AND REPLACE(UPPER(json_extract(summary_json, '$.rcl_num')), ' ', '')"
            " = REPLACE(UPPER(?), ' ', '') ORDER BY number LIMIT 1",
            (term, f"{PRE_PRINT_PREFIX}%", f"{RCL_PREFIX}%", f"{WYKAZ_PREFIX}%", rm_number),
        ).fetchone()
        return self._row_to_bill(row) if row else None

    def save_rcl_num(self, term: int, number: str, rcl_num: str) -> None:
        self._conn.execute(
            "UPDATE bills SET summary_json = json_set(summary_json, '$.rcl_num', ?)"
            " WHERE term = ? AND number = ?",
            (rcl_num, term, number),
        )

    def prints_by_rcl_num(self, term: int) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT number, json_extract(summary_json, '$.rcl_num') FROM bills WHERE term = ?"
            " AND number NOT LIKE ? AND number NOT LIKE ? AND number NOT LIKE ?"
            " AND json_extract(summary_json, '$.rcl_num') IS NOT NULL ORDER BY number DESC",
            (term, f"{PRE_PRINT_PREFIX}%", f"{RCL_PREFIX}%", f"{WYKAZ_PREFIX}%"),
        ).fetchall()
        return {rcl_num_key(rcl_num): number for number, rcl_num in rows}

    def remember_rcl_wykaz_number(
        self, wykaz_number: str, project_id: int, created: date | None
    ) -> None:
        self._conn.execute(
            "INSERT INTO rcl_wykaz_numbers(wykaz_number, project_id, created) VALUES (?, ?, ?)"
            " ON CONFLICT(wykaz_number, project_id)"
            " DO UPDATE SET created = COALESCE(excluded.created, created)",
            (wykaz_number, project_id, created.isoformat() if created else None),
        )

    def find_rcl_project_of_plan(self, wykaz_number: str, announced: date) -> int | None:
        """The newest project published under this number since the plan was announced.

        A project older than the plan is a different bill that held the number before it, and a
        row whose creation date the listing did not give matches nothing: the price of a wrong
        link is a thread about one bill under the card of another.
        """
        row = self._conn.execute(
            "SELECT project_id FROM rcl_wykaz_numbers WHERE wykaz_number = ? AND created >= ?"
            " ORDER BY created DESC, project_id DESC LIMIT 1",
            (wykaz_number, announced.isoformat()),
        ).fetchone()
        return int(row[0]) if row else None

    def _find_rcl(self, path: str, value: str) -> Bill | None:
        row = self._conn.execute(
            f"SELECT * FROM bills WHERE json_extract(rcl_json, '{path}') = ?"
            " ORDER BY term DESC, number LIMIT 1",
            (value,),
        ).fetchone()
        return self._row_to_bill(row) if row else None

    def list_government_rollover_candidates(self, term: int) -> list[Bill]:
        return [self._row_to_bill(row) for row in self._government_rollover_rows(term)]

    def _government_rollover_rows(self, term: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM bills WHERE term = ? AND status NOT IN (?, ?, ?, ?)"
            " AND ((number LIKE ? AND json_extract(rcl_json, '$.print_number') IS NULL)"
            "      OR number LIKE ?) ORDER BY number",
            (
                term,
                BillStatus.LINKED.value,
                BillStatus.BATCH_PENDING.value,
                BillStatus.ANALYSIS_READY.value,
                BillStatus.REANALYSIS_READY.value,
                f"{RCL_PREFIX}%",
                f"{WYKAZ_PREFIX}%",
            ),
        ).fetchall()

    def move_government_rows(self, from_term: int, to_term: int) -> int:
        with self.atomic():
            rows = self._government_rollover_rows(from_term)
            numbers = [str(row["number"]) for row in rows]
            if not numbers:
                return 0
            placeholders = ",".join("?" for _ in numbers)
            if from_term != to_term:
                for row in rows:
                    self._conn.execute(
                        "UPDATE bills SET analysis_json=? WHERE term=? AND number=?"
                        " AND json_type(analysis_json, '$.modern_primary_ref') IS NOT NULL",
                        (row["analysis_json"], from_term, row["number"]),
                    )
            if 0 < from_term < to_term:
                for row in rows:
                    number = row["number"]
                    if number.startswith(RCL_PREFIX) and row["rcl_json"]:
                        project = RclProject.model_validate_json(row["rcl_json"])
                        canonical = rcl_number(project.id) if project.id > 0 else None
                    elif number.startswith(WYKAZ_PREFIX) and row["wykaz_json"]:
                        entry = WykazEntry.model_validate_json(row["wykaz_json"])
                        canonical = wykaz_number(entry.number)
                    else:
                        canonical = None
                    if canonical == number:
                        moved = self._conn.execute(
                            "INSERT INTO government_owner_moves(number,from_term,to_term)"
                            " VALUES (?,?,?) ON CONFLICT(number,from_term) DO UPDATE"
                            " SET to_term=excluded.to_term"
                            " WHERE government_owner_moves.to_term=excluded.to_term",
                            (number, from_term, to_term),
                        )
                        if moved.rowcount != 1:
                            raise ValueError("Conflicting government owner move")
            for table in ("publications", "status_changes", "source_checks"):
                self._conn.execute(
                    f"UPDATE {table} SET term = ? WHERE term = ? AND number IN ({placeholders})",
                    (to_term, from_term, *numbers),
                )
            self._conn.execute(
                "UPDATE bills SET term = ?, summary_json = json_set(summary_json, '$.term', ?)"
                f" WHERE term = ? AND number IN ({placeholders})",
                (to_term, to_term, from_term, *numbers),
            )
            return len(numbers)

    # Sejm rows the chamber never finished with: no closure, not passed. The government's own
    # rows (RCL projects, wykaz entries) are not bound to a term and a new Sejm does not end
    # them; linked rows live on under their print number.
    _UNFINISHED = (
        "number NOT LIKE ? AND number NOT LIKE ? AND status != ? AND discontinued_at IS NULL"
        " AND closure_date IS NULL AND (passed IS NULL OR passed = 0)"
    )
    _UNFINISHED_PARAMS = (f"{RCL_PREFIX}%", f"{WYKAZ_PREFIX}%", BillStatus.LINKED.value)

    def list_unfinished_published(self, term: int, channel_id: str) -> list[Bill]:
        rows = self._conn.execute(
            f"""
            SELECT * FROM bills
            WHERE term = ? AND {self._UNFINISHED}
              AND EXISTS (
                  SELECT 1 FROM publications p
                  WHERE p.term = bills.term AND p.number = bills.number
                    AND p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?
              )
            ORDER BY number
            """,
            (term, *self._UNFINISHED_PARAMS, channel_id),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def discontinue_unfinished(self, term: int, *, at: datetime) -> int:
        cur = self._conn.execute(
            f"UPDATE bills SET discontinued_at = ? WHERE term = ? AND {self._UNFINISHED}",
            (at.isoformat(), term, *self._UNFINISHED_PARAMS),
        )
        return int(cur.rowcount or 0)

    def link_bills(
        self,
        term: int,
        pre_print_number: str,
        print_number: str,
        *,
        wykaz_number: str | None = None,
    ) -> None:
        previous = self.get(term, pre_print_number)
        if previous is not None and previous.editorial.mnemonic is not None:
            inherited = previous.editorial
            current = self.get(term, print_number)
            if current is not None:
                candidate = current.editorial.proposed or current.editorial.mnemonic
                if candidate is not None:
                    inherited = inherited.select(candidate)
            self.save_editorial(term, print_number, inherited)
        self._conn.execute(
            "UPDATE bills SET linked_number = ?, status = ? WHERE term = ? AND number = ?",
            (print_number, BillStatus.LINKED.value, term, pre_print_number),
        )
        self._conn.execute(
            "UPDATE bills SET linked_number = ?, linked_wykaz_number = ?"
            " WHERE term = ? AND number = ?",
            (pre_print_number, wykaz_number, term, print_number),
        )

    def move_primary_analysis_work(self, term: int, from_number: str, to_number: str) -> None:
        self._conn.execute(
            "UPDATE llm_batch_items SET number = ?"
            " WHERE term = ? AND number = ? AND consumed_at IS NULL"
            " AND call_kind IN ('analysis', 'reanalysis')",
            (to_number, term, from_number),
        )
        self._conn.execute(
            "UPDATE llm_batch_intents SET request_json = json_set(request_json, '$.number', ?)"
            " WHERE json_extract(request_json, '$.term') = ?"
            " AND json_extract(request_json, '$.number') = ?"
            " AND COALESCE(json_extract(request_json, '$.question.kind'),"
            " json_extract(request_json, '$.call_kind')) IN ('analysis', 'reanalysis')",
            (to_number, term, from_number),
        )
        self._conn.execute(
            "UPDATE bills SET (ready_analysis_json, analysis_generation) ="
            " (SELECT ready_analysis_json, analysis_generation FROM bills"
            " WHERE term = ? AND number = ?) WHERE term = ? AND number = ?",
            (term, from_number, term, to_number),
        )
        self._conn.execute(
            "UPDATE bills SET ready_analysis_json = NULL WHERE term = ? AND number = ?",
            (term, from_number),
        )

    def _allocate_publication_id(self) -> int:
        # Operator commands must never address a different message after its row was deleted.
        identity = self._conn.execute(
            "UPDATE publication_sequence SET last_id = last_id + 1 WHERE id = 1 RETURNING last_id"
        ).fetchone()
        assert identity is not None, "the migrated repository provides its publication sequence"
        return int(identity[0])

    def create_publication(self, publication: Publication) -> int:
        """Insert the row or, when its unique key exists, reset that row to the given status.

        Returns the row id, looked up by the natural key: `lastrowid` is unreliable after an
        upsert that took the UPDATE path.
        """
        if publication.kind is PublicationKind.STATUS_UPDATE:
            blocked = self._conn.execute(
                "SELECT id FROM publications WHERE status_change_id=? AND channel_id=?"
                " AND kind='status_update'",
                (publication.status_change_id, publication.channel_id),
            ).fetchone()
            previous = self.publication_by_id(int(blocked[0])) if blocked is not None else None
            if previous is not None and (
                previous.status is PublicationStatus.SENT or source_context_blocked(self, previous)
            ):
                return int(blocked[0])
        else:
            previous = self.get_publication(
                publication.term,
                publication.number,
                publication.kind,
                publication.channel_id,
                ref=publication.ref,
            )
            if previous is not None and source_context_blocked(self, previous):
                assert previous.id is not None, "the stored publication has its physical identity"
                return previous.id
        attention = publication.attention or (
            publication.delivery.attention if publication.delivery else None
        )
        identity = self._allocate_publication_id()
        self._conn.execute(
            """
            INSERT INTO publications (id, term, number, kind, status, channel_id, ref, message_id,
                document_message_ids, status_change_id, created_at, sent_at, error, delivery_json,
                attention_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET status = excluded.status, created_at = excluded.created_at,
                                      error = NULL,
                                      delivery_json = COALESCE(
                                          publications.delivery_json, excluded.delivery_json),
                                      attention_json = COALESCE(
                                          publications.attention_json, excluded.attention_json)
            WHERE publications.kind != 'status_update' OR publications.status != 'sent'
            """,
            (
                identity,
                publication.term,
                publication.number,
                publication.kind.value,
                publication.status.value,
                publication.channel_id,
                publication.ref,
                publication.message_id,
                json.dumps(publication.document_message_ids),
                publication.status_change_id,
                publication.created_at.isoformat(),
                _iso(publication.sent_at),
                publication.error,
                publication.delivery.model_dump_json() if publication.delivery else None,
                attention.model_dump_json() if attention else None,
            ),
        )
        if publication.kind is PublicationKind.STATUS_UPDATE:
            row = self._conn.execute(
                "SELECT id FROM publications WHERE status_change_id = ? AND channel_id = ?"
                " AND kind = 'status_update'",
                (publication.status_change_id, publication.channel_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id FROM publications WHERE term = ? AND number = ? AND channel_id = ?"
                " AND kind = ? AND ref IS ?",
                (
                    publication.term,
                    publication.number,
                    publication.channel_id,
                    publication.kind.value,
                    publication.ref,
                ),
            ).fetchone()
        assert row is not None, "the upsert above left a row under this natural key"
        log.debug(
            "local publication write",
            extra=log_extra(
                bill_term=None,
                bill_number=None,
                publication_id=int(row[0]),
                delivery_status=publication.status.value,
            ),
        )
        return int(row[0])

    def list_digest_candidates(
        self, channel_id: str, *, eligible_before: datetime
    ) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications p WHERE channel_id = ? AND status = 'queued'"
            " AND attempts = 0 AND json_extract(delivery_json, '$.destination') = 'digest_only'"
            " AND julianday(COALESCE(json_extract(delivery_json, '$.digest_eligible_at'),"
            " created_at)) < julianday(?)"
            " AND NOT EXISTS (SELECT 1 FROM digest_event_links WHERE source_publication_id = p.id)"
            " ORDER BY created_at, id",
            (channel_id, eligible_before.isoformat()),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def digest_owner(self, source_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT owner_publication_id FROM digest_event_links WHERE source_publication_id = ?",
            (source_id,),
        ).fetchone()
        return int(row[0]) if row else None

    def digest_sources(self, owner_id: int) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT p.* FROM publications p JOIN digest_event_links l"
            " ON l.source_publication_id = p.id WHERE l.owner_publication_id = ? ORDER BY p.id",
            (owner_id,),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def reserve_digest_events(self, owner_id: int, source_ids: tuple[int, ...]) -> None:
        with self.atomic():
            owner = self.publication_by_id(owner_id)
            if (
                owner is None
                or source_context_blocked(self, owner)
                or owner.kind is not PublicationKind.DIGEST
                or owner.status is not PublicationStatus.QUEUED
                or owner.delivery is None
                or owner.attempts
                or len(set(source_ids)) != len(source_ids)
                or owner.delivery.digest_event_ids != source_ids
            ):
                raise ValueError("digest ownership does not match its saved payload")
            for identity in source_ids:
                source = self.publication_by_id(identity)
                linked = self.digest_owner(identity)
                if (
                    source is None
                    or source_context_blocked(self, source)
                    or source.channel_id != owner.channel_id
                    or source.status is not PublicationStatus.QUEUED
                    or source.attempts
                    or source.delivery is None
                    or source.delivery.destination != "digest_only"
                    or (linked is not None and linked != owner_id)
                ):
                    raise ValueError("digest source is no longer available")
            self._conn.executemany(
                "INSERT OR IGNORE INTO digest_event_links VALUES (?, ?)",
                [(identity, owner_id) for identity in source_ids],
            )

    def freeze_legacy_digest_parts(
        self, original: Publication, parts: tuple[DigestPart, ...], *, created_at: datetime
    ) -> Publication | None:
        if not legacy_digest_root(original) or not parts:
            return None
        assert original.id is not None and original.ref is not None, (
            "legacy_digest_root requires a saved week identity"
        )
        if any(not part.text.strip() or utf16_length(part.text) > MESSAGE_LIMIT for part in parts):
            return None
        source_ids = tuple(identity for part in parts for identity in part.event_ids)
        if any(identity <= 0 for identity in source_ids) or len(set(source_ids)) != len(source_ids):
            return None
        saved = original.delivery
        saved_ids = saved.digest_event_ids if saved else ()
        saved_body = saved is not None and bool(saved.rendered_text.strip())
        if saved_body:
            assert saved is not None, "saved_body requires the existing HTML plan"
            if tuple(part.text for part in parts) != split_html(saved.rendered_text):
                return None
            if any(
                part.event_ids != (saved_ids if index == len(parts) - 1 else ())
                for index, part in enumerate(parts)
            ):
                return None
        elif saved_ids:
            return None
        with self.atomic():
            row = self._conn.execute(
                "SELECT * FROM publications WHERE id = ?", (original.id,)
            ).fetchone()
            linked = self.digest_sources(original.id)
            if (
                row is None
                or self._row_to_publication(row) != original
                or self.digest_owner(original.id) is not None
                or (linked and {part.id for part in linked} != set(saved_ids))
                or (linked and not saved_body)
                or self._conn.execute(
                    "SELECT 1 FROM publications WHERE channel_id = ?"
                    " AND kind = 'digest' AND ref LIKE ?",
                    (original.channel_id, original.ref + ":part:%"),
                ).fetchone()
                is not None
            ):
                return None
            sources: dict[int, Publication] = {}
            for identity in source_ids:
                source = self.publication_by_id(identity)
                if (
                    source is None
                    or source.channel_id != original.channel_id
                    or source.kind is PublicationKind.DIGEST
                    or source.status is not PublicationStatus.QUEUED
                    or source.attempts
                    or source.delivery is None
                    or source.delivery.destination != "digest_only"
                    or self.digest_owner(identity) not in (None, original.id)
                ):
                    return None
                sources[identity] = source
            identities = [original.id]
            for index, part in enumerate(parts[1:], start=2):
                child = Publication(
                    term=DIGEST_TERM,
                    number=DIGEST_NUMBER,
                    kind=PublicationKind.DIGEST,
                    status=PublicationStatus.QUEUED,
                    channel_id=original.channel_id,
                    ref=f"{original.ref}:part:{index}",
                    created_at=created_at,
                    delivery=DeliveryPlan(
                        bill_json="",
                        rendered_text=part.text,
                        digest_event_ids=part.event_ids,
                        policy_version="attention-v1",
                        digest_ref=original.ref,
                    ),
                )
                identity = self.create_publication(child)
                if self.publication_by_id(identity) != child.model_copy(update={"id": identity}):
                    raise ValueError("Cannot adopt a mismatched legacy digest part")
                identities.append(identity)
            raw = (
                json.loads(row["delivery_json"])
                if row["delivery_json"] is not None
                else DeliveryPlan(bill_json="", policy_version="attention-v1").model_dump(
                    mode="json"
                )
            )
            raw.update(
                rendered_text=parts[0].text,
                digest_parts=identities,
                digest_ref=original.ref,
                digest_event_ids=parts[0].event_ids,
            )
            if not saved_body:
                raw["render_origin"] = None
            changed = self._conn.execute(
                "UPDATE publications SET delivery_json = ? WHERE id = ? AND status = ?"
                " AND attempts = ? AND delivery_json IS ?",
                (
                    json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                    original.id,
                    original.status.value,
                    original.attempts,
                    row["delivery_json"],
                ),
            )
            if changed.rowcount != 1:
                raise ValueError("Cannot freeze a changed legacy digest root")
            for owner, part in zip(identities, parts, strict=True):
                for identity in part.event_ids:
                    linked_owner = self.digest_owner(identity)
                    if linked_owner is None:
                        self._conn.execute(
                            "INSERT INTO digest_event_links VALUES (?, ?)", (identity, owner)
                        )
                    elif linked_owner != owner:
                        changed = self._conn.execute(
                            "UPDATE digest_event_links SET owner_publication_id = ?"
                            " WHERE source_publication_id = ? AND owner_publication_id = ?",
                            (owner, identity, original.id),
                        )
                        if changed.rowcount != 1:
                            raise ValueError("Cannot move changed legacy digest ownership")
                    if self.digest_owner(identity) != owner:
                        raise ValueError("Cannot reserve legacy digest ownership")
            if any(
                self.publication_by_id(identity) != source for identity, source in sources.items()
            ):
                raise ValueError("Cannot retain changed legacy digest sources")
            frozen = self.publication_by_id(original.id)
            expected = original.model_copy(update={"delivery": DeliveryPlan.model_validate(raw)})
            if frozen != expected:
                raise ValueError("Cannot retain the exact legacy digest root")
            return frozen

    def complete_digest_events(
        self, owner_id: int, *, message_id: int, sent_at: datetime
    ) -> list[Publication]:
        owner = self.publication_by_id(owner_id)
        sources = self.digest_sources(owner_id)
        if (
            owner is None
            or source_context_blocked(self, owner)
            or owner.delivery is None
            or set(owner.delivery.digest_event_ids) != {source.id for source in sources}
        ):
            raise ValueError("digest membership does not match its saved payload")
        for source in sources:
            if (
                source_context_blocked(self, source)
                or source.status is not PublicationStatus.QUEUED
                or source.attempts
                or source.delivery is None
            ):
                raise ValueError("digest source cannot be confirmed")
        for source in sources:
            assert source.id is not None
            self.mark_publication(
                source.id,
                PublicationStatus.SENT,
                message_id=message_id,
                sent_at=sent_at,
                count_attempt=False,
            )
        return sources

    def list_due_deliveries(self, channel_id: str, *, max_attempts: int) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE channel_id = ? AND delivery_json IS NOT NULL"
            " AND kind != 'status_update'"
            " AND (kind != 'message_part' OR ref IS NULL OR ref NOT GLOB ?)"
            " AND (status = 'queued' OR (status = 'failed' AND attempts < ?)) ORDER BY id",
            (channel_id, f"{GROUPED_STAGE_REVISION_REF_PREFIX}*", max_attempts),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def publication_by_id(self, publication_id: int) -> Publication | None:
        row = self._conn.execute(
            "SELECT * FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
        return self._row_to_publication(row) if row else None

    def resolve_delivery(self, resolution: DeliveryResolution) -> bool:
        if isinstance(resolution, GroupedEditDeliveryResolution):
            return False
        status = resolution.status.value
        with self.atomic():
            if self._conn.execute(
                "SELECT 1 FROM delivery_resolutions WHERE command_update_id = ?",
                (resolution.command.update_id,),
            ).fetchone():
                return False
            if self.digest_owner(resolution.publication_id) is not None:
                return False
            original = self.publication_by_id(resolution.publication_id)
            if (
                original is None
                or is_grouped_stage_revision_slot(original)
                or source_context_blocked(self, original)
            ):
                return False
            plan = original.delivery
            identities = (
                plan.publication_ids
                if plan and plan.publication_ids
                else (resolution.publication_id,)
            )
            if resolution.publication_id not in identities:
                return False
            for identity in identities:
                member = self.publication_by_id(identity)
                if member is not None and (
                    is_grouped_stage_revision_slot(member) or source_context_blocked(self, member)
                ):
                    return False
                if (
                    member is None
                    or not (
                        member.status is PublicationStatus.UNKNOWN
                        or (
                            resolution.action == "retry"
                            and member.kind is PublicationKind.DIGEST
                            and member.status is PublicationStatus.FAILED
                            and member.attempts >= 3
                        )
                        or self._failed_card_part_resolution_allowed(member, resolution.action)
                    )
                    or member.channel_id != resolution.channel_id
                    or (
                        plan
                        and plan.publication_ids
                        and (
                            member.delivery is None
                            or member.delivery.publication_ids != plan.publication_ids
                            or member.delivery.group_text != plan.group_text
                        )
                    )
                ):
                    return False
            digest_members = self.digest_sources(resolution.publication_id)
            if (
                original is not None
                and original.delivery is not None
                and set(original.delivery.digest_event_ids)
                != {member.id for member in digest_members}
            ):
                return False
            if any(
                source_context_blocked(self, member)
                or member.status is not PublicationStatus.QUEUED
                or member.delivery is None
                or member.attempts
                for member in digest_members
            ):
                return False
            affected = list(identities)
            for identity in identities:
                self._conn.execute(
                    "UPDATE publications SET status = ?, message_id = COALESCE(?, message_id),"
                    " sent_at = ?, error = NULL,"
                    " attempts = CASE WHEN ? = 'queued' THEN 0 ELSE attempts END"
                    " WHERE id = ?",
                    (
                        status,
                        resolution.message_id,
                        resolution.resolved_at.isoformat() if status == "sent" else None,
                        status,
                        identity,
                    ),
                )
                if status == "dismissed":
                    self._dismiss_delivery_effects(identity, resolution.reason)
            if status == "dismissed":
                root = (
                    self.publication_by_id(plan.continuation_root)
                    if plan is not None and plan.continuation_root is not None
                    else original
                )
                stopped = {resolution.publication_id}
                if root is not None and root.delivery is not None:
                    for child_id in root.delivery.continuation_ids:
                        child = self.publication_by_id(child_id)
                        if (
                            child is not None
                            and child.delivery is not None
                            and (
                                child.delivery.continuation_parent in stopped
                                and child.status is PublicationStatus.QUEUED
                                and child.attempts == 0
                            )
                        ):
                            self.mark_publication(
                                child_id,
                                PublicationStatus.DISMISSED,
                                error="Previous physical part dismissed by operator.",
                                count_attempt=False,
                            )
                            self._dismiss_delivery_effects(child_id, resolution.reason)
                            stopped.add(child_id)
                            affected.append(child_id)
                if original.kind is PublicationKind.DIGEST and original.ref:
                    root = self.get_publication(
                        original.term,
                        original.number,
                        original.kind,
                        original.channel_id,
                        ref=original.ref.split(":part:", 1)[0],
                    )
                    if root is not None and root.delivery is not None:
                        tail = False
                        for identity in root.delivery.digest_parts:
                            if identity == original.id:
                                tail = True
                                continue
                            child = self.publication_by_id(identity)
                            if not tail or child is None:
                                continue
                            if child.status is not PublicationStatus.QUEUED or child.attempts:
                                break
                            self.mark_publication(
                                identity,
                                PublicationStatus.DISMISSED,
                                error="Previous physical part dismissed by operator.",
                                count_attempt=False,
                            )
                            self._dismiss_delivery_effects(identity, resolution.reason)
                            affected.append(identity)
            self._conn.execute(
                "INSERT INTO delivery_resolutions"
                " (publication_id, command_update_id, resolution_json) VALUES (?, ?, ?)",
                (
                    resolution.publication_id,
                    resolution.command.update_id,
                    resolution.model_copy(
                        update={"publication_ids": tuple(affected)}
                    ).model_dump_json(),
                ),
            )
        return True

    def recover_source_context_retry(
        self,
        resolution: DeliveryResolution,
        *,
        delivery: DeliveryPlan,
        version: ContentVersion,
        snapshots: tuple[ContentSnapshot, ...],
    ) -> bool:
        with self.atomic():
            original = self.publication_by_id(resolution.publication_id)
            if (
                original is None
                or original.error != SOURCE_CONTEXT_INVALID
                or original.status is not PublicationStatus.QUEUED
                or original.attempts
                or original.delivery is None
                or original.delivery.rendered_text
                or not supported_source_context(original)
                or resolution.action != "retry"
                or resolution.publication_ids != (resolution.publication_id,)
                or (resolution.term, resolution.number, resolution.kind, resolution.channel_id)
                != (original.term, original.number, original.kind, original.channel_id)
                or self.digest_owner(resolution.publication_id) is not None
                or self.get_publication_content(resolution.publication_id) is not None
                or resolution.resolved_at.tzinfo is None
                or resolution.resolved_at.utcoffset() is None
                or self._conn.execute(
                    "SELECT 1 FROM delivery_resolutions WHERE command_update_id=?",
                    (resolution.command.update_id,),
                ).fetchone()
                is not None
            ):
                return False
            plan = original.delivery
            today = (
                plan.today
                if plan.edit_message_id is not None
                else resolution.resolved_at.astimezone(ZoneInfo("Europe/Warsaw")).date()
            )
            if (
                plan.model_dump(exclude={"rendered_text", "today"})
                != delivery.model_dump(exclude={"rendered_text", "today"})
                or not delivery.rendered_text.strip()
                or utf16_length(delivery.rendered_text) > MESSAGE_LIMIT
                or not supported_source_context(original.model_copy(update={"delivery": delivery}))
                or delivery.today != today
                or plan.edit_message_id is not None
                and plan.today is None
                or version.source_scope != "frozen_primary_context/analysis"
                or len(version.source_observation_ids) != 1
                or version.root_publication_id
                != (
                    plan.revision_of
                    if original.kind is PublicationKind.AMENDMENTS_REVISION
                    else original.id
                )
                or version.reason
                != (
                    "amendments_revision"
                    if original.kind is PublicationKind.AMENDMENTS_REVISION
                    else "original"
                )
            ):
                return False
            expected = (
                editorial_bill_snapshot(plan.bill_json),
                ContentSnapshot(kind="change", payload_json=plan.change_json),
                ContentSnapshot(
                    kind="plan",
                    payload_json=delivery.model_copy(
                        update={"bill_json": "", "change_json": "", "rendered_text": ""}
                    ).model_dump_json(),
                ),
            )
            if snapshots != expected or (
                version.bill_snapshot_id,
                version.change_snapshot_id,
                version.plan_snapshot_id,
            ) != tuple(item.snapshot_id for item in expected):
                return False
            if (
                content_delivery_mismatch(
                    version, {snapshot.snapshot_id: snapshot for snapshot in snapshots}, delivery
                )
                is not None
            ):
                return False
            for snapshot in snapshots:
                self.save_content_snapshot(snapshot)
            known = inspect_primary_content_sources(
                ContentSourceRows(self._conn),
                bill_snapshot_id=version.bill_context_id,
                change_snapshot_id=version.change_snapshot_id,
            )
            if (
                known.state != "known"
                or known.source_observation_ids != version.source_observation_ids
            ):
                raise SourceContextInvalidError("Recovery requires the original known source chain")
            self._conn.execute(
                "UPDATE publications SET delivery_json=? WHERE id=?",
                (delivery.model_dump_json(), resolution.publication_id),
            )
            self.save_content_version(version)
            self.bind_publication_content(resolution.publication_id, version.content_id)
            self._conn.execute(
                "INSERT INTO delivery_resolutions(publication_id,command_update_id,resolution_json)"
                " VALUES(?,?,?)",
                (
                    resolution.publication_id,
                    resolution.command.update_id,
                    resolution.model_dump_json(),
                ),
            )
            self._conn.execute(
                "UPDATE publications SET error=NULL WHERE id=?", (resolution.publication_id,)
            )
        return True

    def _failed_card_part_resolution_allowed(self, publication: Publication, action: str) -> bool:
        plan = publication.delivery
        root = (
            self.publication_by_id(plan.continuation_root)
            if plan is not None and plan.continuation_root is not None
            else publication
        )
        owner_id = card_edit_owner_id(root) if root is not None else None
        owner = self.publication_by_id(owner_id) if owner_id is not None else None
        return failed_card_part_resolution_allowed(publication, root, owner, action)

    def _dismiss_delivery_effects(self, publication_id: int, reason: str | None) -> None:
        publication = self.publication_by_id(publication_id)
        if publication is None or publication.delivery is None:
            return
        plan = publication.delivery
        owner_id = plan.continuation_root or publication_id
        for change_id in plan.held_change_ids:
            owned = self.get_update_publication(change_id, publication.channel_id)
            if (
                owned is not None
                and owned.id is not None
                and (
                    owned.status is PublicationStatus.SKIPPED
                    or (
                        owned.status is PublicationStatus.QUEUED
                        and owned.delivery is not None
                        and owned.delivery.absorbed_by == owner_id
                    )
                )
            ):
                self.mark_publication(
                    owned.id, PublicationStatus.DISMISSED, error=reason, count_attempt=False
                )
        for source in self.digest_sources(publication_id):
            if (
                source.id is not None
                and source.status is PublicationStatus.QUEUED
                and not source.attempts
            ):
                self.mark_publication(
                    source.id, PublicationStatus.DISMISSED, error=reason, count_attempt=False
                )
                self._dismiss_delivery_effects(source.id, reason)

    def delivery_resolutions(self, publication_id: int) -> list[DeliveryResolution]:
        rows = self._conn.execute(
            "SELECT resolution_json FROM delivery_resolutions"
            " WHERE publication_id = ? OR EXISTS ("
            " SELECT 1 FROM json_each(resolution_json, '$.publication_ids') WHERE value = ?)"
            " ORDER BY id",
            (publication_id, publication_id),
        ).fetchall()
        decisions = [read_delivery_resolution(row[0]) for row in rows]
        for decision in decisions:
            if isinstance(decision, GroupedEditDeliveryResolution):
                version = self.get_publication_content(publication_id)
                if (
                    version is None
                    or version.reason != "grouped_stage_revision"
                    or decision.publication_id != publication_id
                ):
                    raise ValueError("grouped_resolution_orphan")
        return decisions

    def pin_grouped_telegram_receipt(
        self,
        publication_ids: tuple[int, int],
        *,
        content_id: str,
        chat_id: int,
        message_id: int,
        sent_at: datetime,
    ) -> None:
        with self.atomic():
            root_id, second_id = publication_ids
            changed = self._conn.execute(
                "UPDATE publications SET telegram_chat_id=?"
                " WHERE id IN (?,?) AND ref=? AND kind='status_update' AND status='sent'"
                " AND message_id=? AND sent_at=? AND telegram_chat_id IS NULL"
                " AND EXISTS (SELECT 1 FROM publication_content"
                " WHERE publication_id=? AND content_id=?)",
                (
                    chat_id,
                    root_id,
                    second_id,
                    f"updates/{root_id}",
                    message_id,
                    _iso(sent_at),
                    root_id,
                    content_id,
                ),
            ).rowcount
            if changed != 2:
                raise StateCheckpointError("Grouped Telegram receipt no longer owns both sent rows")

    def mark_publication(
        self,
        publication_id: int,
        status: PublicationStatus,
        *,
        message_id: int | None = None,
        document_message_ids: list[int] | None = None,
        error: str | None = None,
        sent_at: datetime | None = None,
        count_attempt: bool = True,
    ) -> None:
        original = self.publication_by_id(publication_id)
        if original is not None and source_context_blocked(self, original):
            if (
                status == original.status
                and error == SOURCE_CONTEXT_INVALID
                and message_id is None
                and document_message_ids is None
                and sent_at is None
            ):
                return
            raise ValueError("source_context_invalid requires explicit validated repair")
        self._conn.execute(
            """
            UPDATE publications SET status = ?,
                message_id = COALESCE(?, message_id),
                document_message_ids = COALESCE(?, document_message_ids),
                error = ?, sent_at = COALESCE(?, sent_at),
                attempts = attempts + ?
            WHERE id = ?
            """,
            (
                status.value,
                message_id,
                json.dumps(document_message_ids) if document_message_ids is not None else None,
                error[:2000] if error else None,
                _iso(sent_at),
                int(status is PublicationStatus.FAILED and count_attempt),
                publication_id,
            ),
        )
        if original is not None:
            log.debug(
                "local publication status write",
                extra=log_extra(
                    bill_term=original.term,
                    bill_number=original.number,
                    publication_id=publication_id,
                    delivery_status=status.value,
                ),
            )

    def get_publication(
        self,
        term: int,
        number: str,
        kind: PublicationKind,
        channel_id: str,
        *,
        ref: str | None = None,
    ) -> Publication | None:
        sql = (
            "SELECT * FROM publications"
            " WHERE term = ? AND number = ? AND kind = ? AND channel_id = ?"
        )
        params: list[object] = [term, number, kind.value, channel_id]
        if ref is not None:
            sql += " AND ref = ?"
            params.append(ref)
        row = self._conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
        return self._row_to_publication(row) if row else None

    def list_publications_between(
        self, channel_id: str, *, since: datetime, until: datetime
    ) -> list[Publication]:
        """Every post the channel sent in the half-open window, oldest first; `sent` rows
        alone, the digest being a reading of the channel and not of the database."""
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE channel_id = ? AND status = ?"
            " AND sent_at >= ? AND sent_at < ? ORDER BY sent_at, id",
            (
                channel_id,
                PublicationStatus.SENT.value,
                _utc_iso(since),
                _utc_iso(until),
            ),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def list_attention_publications(
        self, term: int, number: str, *, limit: int
    ) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE term = ? AND number = ?"
            " AND attention_json IS NOT NULL"
            " ORDER BY id DESC LIMIT ?",
            (term, number, limit),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def delete_publication(
        self, term: int, number: str, kind: PublicationKind, channel_id: str
    ) -> int:
        """Operator action: forget a post so the normal path can send it again. Returns the
        number of rows removed (0 or 1)."""
        with self.atomic():
            if kind is PublicationKind.NEW_BILL:
                self._conn.execute(
                    "INSERT INTO retired_stage_anchors"
                    "(id,term,number,channel_id,message_id,sent_at)"
                    " SELECT p.id,p.term,p.number,p.channel_id,p.message_id,p.sent_at"
                    " FROM publications p WHERE p.term=? AND p.number=? AND p.channel_id=?"
                    " AND p.kind='new_bill' AND p.status='sent' AND p.message_id>0"
                    " AND p.sent_at IS NOT NULL AND EXISTS ("
                    " SELECT 1 FROM content_versions v WHERE"
                    " json_extract(v.version_json,"
                    " '$.direct_stage_evidence.anchor_publication_id')=p.id"
                    " OR json_extract(v.version_json,"
                    " '$.original_delivery_evidence.joint_primary.publication_id')=p.id"
                    " OR EXISTS (SELECT 1 FROM json_each(v.version_json,"
                    " '$.grouped_stage_evidence.members') member"
                    " WHERE json_extract(member.value, '$.anchor_publication_id')=p.id))",
                    (term, number, channel_id),
                )
            if kind in (PublicationKind.NEW_BILL, PublicationKind.JOINT_BILL):
                self._conn.execute(
                    "INSERT INTO retired_card_roots"
                    "(id,content_id,term,number,channel_id,kind,ref,created_at,message_id,sent_at)"
                    " SELECT p.id,v.content_id,p.term,p.number,p.channel_id,p.kind,p.ref,"
                    " p.created_at,p.message_id,p.sent_at FROM publications p"
                    " JOIN publication_content c ON c.publication_id=p.id"
                    " JOIN content_versions v ON v.content_id=c.content_id"
                    " WHERE p.term=? AND p.number=? AND p.kind=? AND p.channel_id=?"
                    " AND p.status='sent' AND p.message_id>0 AND p.sent_at IS NOT NULL"
                    " AND v.root_publication_id=p.id"
                    " AND (json_extract(v.version_json,'$.original_delivery_evidence.kind')=p.kind"
                    " OR (p.kind='new_bill' AND json_type(v.version_json,'$.card_context_profile')"
                    " IS NOT NULL AND EXISTS (SELECT 1 FROM content_versions edition"
                    " WHERE edition.root_publication_id=p.id AND json_type(edition.version_json,"
                    " '$.multipart_card_revision') IS NOT NULL)))",
                    (term, number, kind.value, channel_id),
                )
            cur = self._conn.execute(
                "DELETE FROM publications"
                " WHERE term = ? AND number = ? AND kind = ? AND channel_id = ?",
                (term, number, kind.value, channel_id),
            )
            return int(cur.rowcount)

    def card_aliases(self, channel_id: str, message_id: int) -> list[Publication]:
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE channel_id = ? AND message_id = ?"
            " AND kind = 'new_bill' AND status = 'sent' ORDER BY id",
            (channel_id, message_id),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def card_refresh_parts(self, card_id: int) -> list[Publication]:
        return [self._row_to_publication(row) for row in card_refresh_rows(self._conn, card_id)]

    def card_may_need_partial_repair(self, channel_id: str, message_id: int, digest: str) -> bool:
        return (
            self._conn.execute(
                """
            SELECT 1 FROM publications child JOIN publications root ON root.id=CASE
                WHEN json_valid(child.delivery_json) THEN
                    CAST(json_extract(child.delivery_json, '$.continuation_root') AS INTEGER) END
            WHERE child.channel_id=? AND child.kind='message_part' AND child.status='dismissed'
            AND root.channel_id=child.channel_id AND root.kind='message_part' AND root.status='sent'
            AND root.ref GLOB 'card/*/*/1' AND root.message_id=? AND root.rendered_sha256 IS NOT ?
            AND NOT EXISTS (
                SELECT 1 FROM publications later
                WHERE later.id>root.id AND later.channel_id=root.channel_id
                AND later.message_id=root.message_id AND later.status='sent'
                AND later.kind='message_part' AND later.ref GLOB 'card/*/*/1'
            ) LIMIT 1
            """,
                (channel_id, message_id, digest),
            ).fetchone()
            is not None
        )

    def set_card_digest(self, publication_id: int, digest: str) -> None:
        """Remember what the card said when it was last sent or edited."""
        self._conn.execute(
            "UPDATE publications SET rendered_sha256 = ? WHERE id = ?", (digest, publication_id)
        )

    def mark_stale_pending_as_unknown(self, *, now: datetime) -> list[str]:
        """Settle the rows a crashed run left behind, and name them: they are never re-sent, so
        this one line is the only trace the reader's missing post leaves anywhere."""
        rows = self._conn.execute(
            "SELECT number, kind FROM publications WHERE status = ? ORDER BY number, kind",
            (PublicationStatus.PENDING.value,),
        ).fetchall()
        self._conn.execute(
            "UPDATE publications SET status = ?, error = ? WHERE status = ?",
            (
                PublicationStatus.UNKNOWN.value,
                f"left pending by a crashed run; marked unknown at {now.isoformat()}",
                PublicationStatus.PENDING.value,
            ),
        )
        return [f"{r['number']} {r['kind']}" for r in rows]

    def add_status_change(self, change: StatusChange) -> int | None:
        validate_supplement_dependencies(self._conn, change.supplements)
        if change.amendments is not None:
            validate_secondary_dependency(self._conn, change.amendments, "amendments")
        try:
            cur = self._conn.execute(
                """
                INSERT INTO status_changes (term, number, old_fingerprint, new_fingerprint,
                                            new_stages_json, closure_detected, passed,
                                            content_changed, withdrawn, discontinued,
                                            amendments_json, supplements_json, detected_at,
                                            consultation_opened, decision_changed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change.term,
                    change.number,
                    change.old_fingerprint,
                    change.new_fingerprint,
                    json.dumps(
                        [s.model_dump(mode="json") for s in change.new_stages], ensure_ascii=False
                    ),
                    int(change.closure_detected),
                    _bool(change.passed),
                    int(change.content_changed),
                    int(change.withdrawn),
                    int(change.discontinued),
                    (
                        change.amendments.model_dump_json()
                        if change.amendments is not None
                        else None
                    ),
                    _supplements_json(change.supplements),
                    change.detected_at.isoformat(),
                    int(change.consultation_opened),
                    int(change.decision_changed),
                ),
            )
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid or 0)

    def save_status_change_amendments(self, change_id: int, record: AmendmentsRecord) -> None:
        validate_secondary_dependency(self._conn, record, "amendments")
        self._conn.execute(
            "UPDATE status_changes SET amendments_json = ? WHERE id = ?",
            (
                self._preserve_legacy_json(
                    record.model_dump_json(),
                    "SELECT amendments_json FROM status_changes WHERE id = ?",
                    (change_id,),
                ),
                change_id,
            ),
        )

    def save_status_change_supplements(
        self, change_id: int, records: list[SupplementRecord]
    ) -> None:
        validate_supplement_dependencies(self._conn, records)
        encoded = _supplements_json(records)
        preserved = self._preserve_legacy_json(
            encoded if encoded is not None else "[]",
            "SELECT supplements_json FROM status_changes WHERE id = ?",
            (change_id,),
        )
        self._conn.execute(
            "UPDATE status_changes SET supplements_json = ? WHERE id = ?",
            (preserved if encoded is not None else None, change_id),
        )

    def closure_announced(self, term: int, number: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM status_changes WHERE term = ? AND number = ? AND closure_detected = 1"
            " LIMIT 1",
            (term, number),
        ).fetchone()
        return row is not None

    def list_held_status_changes(
        self, term: int, number: str, channel_id: str
    ) -> list[StatusChange]:
        """Changes of the bill held back as service stages (a `skipped` update row), oldest
        first: the next post of the bill lists their stages too."""
        rows = self._conn.execute(
            """
            SELECT c.* FROM status_changes c
            JOIN publications p ON p.status_change_id = c.id AND p.kind = 'status_update'
            WHERE c.term = ? AND c.number = ? AND p.channel_id = ? AND p.status = 'skipped'
              AND NOT EXISTS (
                  SELECT 1 FROM publications planned,
                    json_each(planned.delivery_json, '$.held_change_ids') held
                  WHERE held.value = c.id AND planned.channel_id = p.channel_id
                    AND planned.status != 'skipped'
              )
            ORDER BY c.id
            """,
            (term, number, channel_id),
        ).fetchall()
        return [self._row_to_status_change(r) for r in rows]

    def save_document_work(self, work: DocumentWork) -> None:
        existing = self._conn.execute(
            "SELECT * FROM document_work WHERE key=?", (work.key,)
        ).fetchone()
        if existing is not None:
            previous = read_document_work(self._conn, existing)
            if not isinstance(previous, DocumentWork):
                raise ValueError("a Senate continuation cannot replace revision application work")
            if (
                previous.senate_input_witness is not None
                and previous.senate_input_witness != work.senate_input_witness
                or work.senate_input_witness is not None
                and (previous.term, previous.number, previous.change_id)
                != (work.term, work.number, work.change_id)
            ):
                raise ValueError("a Senate continuation cannot replace its frozen input witness")
        validate_senate_input_witness(self._conn, work)
        self._conn.execute(
            "INSERT INTO document_work(key, term, number, state, next_at, work_json) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
            "state=excluded.state, next_at=excluded.next_at, work_json=excluded.work_json",
            (
                work.key,
                work.term,
                work.number,
                work.state,
                _utc_iso(work.next_at),
                work.model_dump_json(),
            ),
        )

    def list_document_work(self, *, due: datetime) -> list[DocumentWork]:
        rows = self._conn.execute(
            "SELECT * FROM document_work WHERE state='waiting' AND next_at <= ? "
            "ORDER BY next_at, key",
            (_utc_iso(due),),
        ).fetchall()
        work = [read_document_work(self._conn, row) for row in rows]
        return [item for item in work if isinstance(item, DocumentWork)]

    def save_revision_application_work(self, work: AmendmentRevisionApplicationWork) -> None:
        work = AmendmentRevisionApplicationWork.model_validate_json(work.model_dump_json())
        with self.atomic():
            validate_revision_application_work(self._conn, work)
            previous = self.get_revision_application_work(work.key)
            if previous is not None:
                if previous.frozen_values() != work.frozen_values():
                    raise ValueError("frozen revision application work cannot be replaced")
                if previous.state == "complete" and work != previous:
                    raise ValueError("completed revision application work cannot be reopened")
                if work.attempts < previous.attempts:
                    raise ValueError("revision application attempts cannot decrease")
            self._conn.execute(
                "INSERT INTO document_work(key, term, number, state, next_at, work_json)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                " state=excluded.state, next_at=excluded.next_at, work_json=excluded.work_json",
                (
                    work.key,
                    work.term,
                    work.number,
                    work.state,
                    _utc_iso(work.next_at),
                    work.model_dump_json(),
                ),
            )

    def get_revision_application_work(self, key: str) -> AmendmentRevisionApplicationWork | None:
        row = self._conn.execute("SELECT * FROM document_work WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        work = read_document_work(self._conn, row)
        if not isinstance(work, AmendmentRevisionApplicationWork):
            raise ValueError("the work key belongs to a Senate continuation")
        return work

    def list_revision_application_work(
        self, *, due: datetime | None = None, publication_id: int | None = None
    ) -> list[AmendmentRevisionApplicationWork]:
        query = "SELECT * FROM document_work WHERE state IN ('waiting','blocked')"
        parameters: tuple[object, ...] = ()
        if due is not None:
            query += " AND state='waiting' AND next_at <= ?"
            parameters += (_utc_iso(due),)
        if publication_id is not None:
            query += " AND json_extract(work_json, '$.original_publication_id')=?"
            parameters += (publication_id,)
        rows = self._conn.execute(query + " ORDER BY next_at, key", parameters).fetchall()
        work = [read_document_work(self._conn, row) for row in rows]
        return [item for item in work if isinstance(item, AmendmentRevisionApplicationWork)]

    def save_revision_application_handoff(
        self,
        batch_id: str,
        custom_id: str,
        *,
        work_key: str | None = None,
        problem: str | None = None,
    ) -> None:
        if (work_key is None) == (problem is None):
            raise ValueError("a revision handoff requires work or an explicit problem")
        row = self._conn.execute(
            "SELECT * FROM llm_batch_items WHERE batch_id=? AND custom_id=?",
            (batch_id, custom_id),
        ).fetchone()
        if row is None or row["call_kind"] != "amendments":
            raise ValueError("a revision handoff requires its retained amendments item")
        item = _row_to_llm_batch_item(row, self._conn)
        if not isinstance(item.meta, DigestItemMeta) or item.meta.amendment_revision is None:
            raise ValueError("a revision handoff requires its captured target")
        if work_key is not None:
            work = self.get_revision_application_work(work_key)
            if work is None or (work.term, work.number, work.request_key) != (
                item.term,
                item.number,
                item.custom_id,
            ):
                raise ValueError("a revision handoff requires matching persisted work")
            retained = self.get_computation_result(work.result_id)
            if retained is None or (retained.batch_id, retained.custom_id) != (batch_id, custom_id):
                raise ValueError("a revision handoff requires its actual retained result")
            meta = item.meta.model_copy(
                update={"amendment_revision": None, "revision_application_work_key": work_key}
            )
        else:
            meta = item.meta.model_copy(update={"revision_application_problem": problem})
        meta = DigestItemMeta.model_validate_json(meta.model_dump_json())
        self._conn.execute(
            "UPDATE llm_batch_items SET meta_json=? WHERE batch_id=? AND custom_id=?",
            (meta.model_dump_json(), batch_id, custom_id),
        )

    def list_status_changes(self, term: int, number: str) -> list[StatusChange]:
        rows = self._conn.execute(
            "SELECT * FROM status_changes WHERE term = ? AND number = ? ORDER BY id",
            (term, number),
        ).fetchall()
        return [self._row_to_status_change(r) for r in rows]

    def get_status_change(self, change_id: int) -> StatusChange | None:
        """The change one update post was about; None when the row is gone."""
        row = self._conn.execute(
            "SELECT * FROM status_changes WHERE id = ?", (change_id,)
        ).fetchone()
        return self._row_to_status_change(row) if row else None

    def release_held_status_changes(
        self, term: int, number: str, channel_id: str, *, message_id: int, sent_at: datetime
    ) -> int:
        """The held changes of the bill went out inside the post `message_id`."""
        cur = self._conn.execute(
            """
            UPDATE publications SET status = 'sent', message_id = ?, sent_at = ?
            WHERE kind = 'status_update' AND status = 'skipped'
              AND term = ? AND number = ? AND channel_id = ?
            """,
            (message_id, sent_at.isoformat(), term, number, channel_id),
        )
        return int(cur.rowcount or 0)

    def list_due_status_changes(self, channel_id: str, *, max_attempts: int) -> list[StatusChange]:
        """Queued news and unexhausted failures are deliverable; ambiguous attempts are not."""
        rows = self._conn.execute(
            """
            SELECT c.* FROM status_changes c
            JOIN publications p ON p.status_change_id = c.id AND p.kind = 'status_update'
            WHERE p.channel_id = ? AND (p.status = 'queued'
                OR (p.status = 'failed' AND p.attempts < ?))
            ORDER BY c.id
            """,
            (channel_id, max_attempts),
        ).fetchall()
        return [self._row_to_status_change(r) for r in rows]

    def last_discovery_started_at(self) -> datetime | None:
        """Start of the last full run whose discovery phase completed (the watermark)."""
        row = self._conn.execute(
            "SELECT started_at FROM runs WHERE discovery_ok = 1 AND mode = ?"
            " ORDER BY started_at DESC LIMIT 1",
            (RunMode.RUN.value,),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def prune_runs(self, *, before: datetime) -> int:
        """Forget run records started before `before`; their reports are in the log channel."""
        cur = self._conn.execute(
            "DELETE FROM runs WHERE started_at < ? AND id NOT IN"
            " (SELECT id FROM runs WHERE discovery_ok = 1 AND mode = 'run'"
            " ORDER BY started_at DESC LIMIT 1)",
            (before.isoformat(),),
        )
        return int(cur.rowcount or 0)

    def _retained_revision_work(
        self, publication_id: int
    ) -> AmendmentRevisionApplicationWork | None:
        version = self.get_publication_content(publication_id)
        if version is None:
            return None
        self._validate_publication_content_binding(publication_id, version.content_id)
        rows = self._conn.execute(
            "SELECT * FROM document_work WHERE"
            " json_extract(work_json, '$.revision_publication_id')=?",
            (publication_id,),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("a revision requires one completed application work item")
        work = read_document_work(self._conn, rows[0])
        if not isinstance(work, AmendmentRevisionApplicationWork) or work.state != "complete":
            return None
        if (
            version.reason != "amendments_revision"
            or version.root_publication_id != work.original_publication_id
        ):
            raise ValueError("retained revision content must identify its completed application")
        if (
            version.rendered_text is None
            or version.formatter_version is None
            or version.language is None
        ):
            return None
        publication = self.publication_by_id(publication_id)
        if publication is None or publication.message_id != work.message_id:
            raise ValueError("retained revision must confirm its original physical target")
        assert version.previous_version_id is not None, "a validated revision names its predecessor"
        previous = self.get_content_version(version.previous_version_id)
        if previous is None or previous.root_publication_id != version.root_publication_id:
            raise ValueError("retained revision content requires its predecessor")
        return work

    def prune_history(self, *, before: datetime, memo_before: datetime) -> dict[str, int]:
        with self.atomic():
            candidates = self._conn.execute(
                "SELECT * FROM publications WHERE status = 'sent'"
                " AND sent_at < ? AND delivery_json IS NOT NULL"
                " AND NOT EXISTS (SELECT 1 FROM document_work w"
                " WHERE json_extract(w.work_json, '$.change_id') = publications.status_change_id"
                " AND w.state != 'complete')"
                " AND NOT EXISTS (SELECT 1"
                " FROM json_each(publications.delivery_json, '$.digest_parts') part"
                " JOIN publications member ON member.id = part.value"
                " WHERE member.status NOT IN ('sent', 'dismissed'))"
                " AND NOT EXISTS (SELECT 1 FROM publications child"
                " WHERE child.status NOT IN ('sent', 'dismissed')"
                " AND json_extract(child.delivery_json, '$.continuation_root') ="
                " COALESCE(json_extract(publications.delivery_json, '$.continuation_root'),"
                " publications.id))",
                (_iso(before),),
            ).fetchall()
            publications = 0
            for row in candidates:
                work = None
                if row["kind"] == "amendments_revision":
                    work = self._retained_revision_work(row["id"])
                    if work is None:
                        continue
                publications += self._conn.execute(
                    "UPDATE publications SET delivery_json=NULL WHERE id=?", (row["id"],)
                ).rowcount
                if work is not None:
                    validate_revision_application_work(self._conn, work)
            self._conn.execute(
                memo_retention_cte(owner_moves=True)
                + """
                DELETE FROM analysis_memo WHERE key IN (
                    SELECT key FROM memo_retention WHERE last_used_at < :cutoff
                    AND has_bill AND settled AND NOT bill_work AND NOT document_work
                    AND NOT publication AND NOT intent AND NOT batch_item
                    AND NOT intent_key_ref AND NOT batch_item_key_ref AND NOT review_key_ref
                )
                """,
                {"cutoff": _iso(memo_before)},
            )
            memos = self._conn.execute("SELECT changes()").fetchone()[0]
            self._conn.execute(
                batch_retention_cte()
                + """
                UPDATE llm_batch_items AS i
                SET meta_json = json_remove(
                    meta_json, '$.text', '$.located', '$.review_context',
                    '$.explanation.text', '$.explanation.located'),
                  result_json = json_remove(result_json, '$.answer', '$.analysis')
                WHERE
                """
                + batch_payload_cleanup_filter()
            )
            items = self._conn.execute("SELECT changes()").fetchone()[0]
            inputs = prune_amendment_inputs(self._conn)
        return {
            "delivery_payloads": publications,
            "analysis_memos": memos,
            "batch_payloads": items,
            **{key: count for key, count in inputs.items() if count},
        }

    def start_run(self, report: RunReport) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (started_at, since, mode) VALUES (?, ?, ?)",
            (report.started_at.isoformat(), report.since.isoformat(), report.mode.value),
        )
        return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, report: RunReport) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at = ?, ok = ?, discovery_ok = ?, report_json = ?"
            " WHERE id = ?",
            (
                _iso(report.finished_at),
                int(report.ok),
                int(report.discovery_ok),
                report.model_dump_json(),
                run_id,
            ),
        )

    def list_runs(self, *, since: datetime) -> list[RunReport]:
        rows = self._conn.execute(
            "SELECT report_json FROM runs WHERE started_at >= ? AND report_json IS NOT NULL"
            " ORDER BY started_at DESC",
            (since.isoformat(),),
        ).fetchall()
        return [RunReport.model_validate_json(r[0]) for r in rows]

    def most_expensive_analyses(self, *, limit: int) -> list[Bill]:
        validate_primary_cost_projections(self._conn)
        rows = self._conn.execute(
            """
            WITH ranked AS (
                SELECT b.*, CASE
                    WHEN json_type(b.analysis_json, '$.modern_primary_ref') IS NOT NULL
                        THEN CASE WHEN json_type(b.analysis_json, '$.observation.input_tokens')
                            IS NOT NULL
                            THEN json_extract(b.analysis_json, '$.observation.input_tokens')
                            ELSE json_extract(c.result_json, '$.record.input_tokens') END
                    WHEN json_type(b.analysis_json, '$.legacy_result_ref') IS NULL
                        THEN json_extract(b.analysis_json, '$.input_tokens')
                    WHEN json_type(b.analysis_json, '$.projection_overrides.input_tokens')
                        IS NOT NULL
                        THEN json_extract(b.analysis_json, '$.projection_overrides.input_tokens')
                    WHEN EXISTS (
                        SELECT 1 FROM json_each(b.analysis_json, '$.projection_removed')
                        WHERE value = 'input_tokens'
                    ) THEN NULL
                    ELSE json_extract(v.envelope_json, '$.input_tokens')
                END AS analysis_input_tokens
                FROM bills b LEFT JOIN legacy_result_values v
                    ON v.value_id = json_extract(b.analysis_json, '$.legacy_result_ref')
                LEFT JOIN computation_results c
                    ON c.result_id = json_extract(b.analysis_json, '$.modern_primary_ref')
            )
            SELECT * FROM ranked WHERE analysis_input_tokens IS NOT NULL
            ORDER BY analysis_input_tokens DESC, term, number
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def record_command(self, command: IncomingCommand) -> bool:
        """Remember the command before it runs; False when the update was recorded already."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO commands (update_id, chat_id, message_id, text, received_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                command.update_id,
                command.chat_id,
                command.message_id,
                command.text,
                command.received_at.isoformat(),
            ),
        )
        return bool(cur.rowcount)

    def command_state(self, update_id: int) -> CommandState | None:
        row = self._conn.execute(
            "SELECT received_at, executed_at, handled_at, reply FROM commands WHERE update_id = ?",
            (update_id,),
        ).fetchone()
        if row is None:
            return None
        return CommandState(
            received_at=_parse_dt(row["received_at"]),
            executed_at=_parse_dt(row["executed_at"]),
            handled_at=_parse_dt(row["handled_at"]),
            reply=row["reply"],
        )

    def mark_command_executed(self, update_id: int, *, outcome: str, at: datetime) -> None:
        self._conn.execute(
            "UPDATE commands SET executed_at = ?, reply = ? WHERE update_id = ?",
            (at.isoformat(), outcome, update_id),
        )

    def mark_command_handled(self, update_id: int, *, reply: str, at: datetime) -> None:
        self._conn.execute(
            "UPDATE commands SET handled_at = ?, reply = ? WHERE update_id = ?",
            (at.isoformat(), reply, update_id),
        )

    def _row_to_bill(self, row: sqlite3.Row) -> Bill:
        columns = row.keys()
        stages = tuple(Stage.model_validate(s) for s in json.loads(row["stages_json"] or "[]"))
        analysis = (
            AnalysisRecord.model_validate_json(row["analysis_json"])
            if row["analysis_json"]
            else None
        )
        bill = Bill(
            summary=ProcessSummary.model_validate_json(row["summary_json"]),
            status=BillStatus(row["status"]),
            prefilter_hits=json.loads(row["prefilter_hits"] or "[]"),
            stages=stages,
            stages_fingerprint=row["stages_fingerprint"],
            observed_closure_date=(
                date.fromisoformat(row["observed_closure_date"])
                if row["observed_closure_date"]
                else None
            ),
            commission_review=DocumentReviewRecord.model_validate_json(
                row["commission_review_json"]
            )
            if "commission_review_json" in columns and row["commission_review_json"]
            else None,
            committee_documents=tuple(
                ReviewSource.model_validate(item)
                for item in json.loads(row["committee_documents_json"])
            )
            if "committee_documents_json" in columns and row["committee_documents_json"]
            else None,
            consultation_review=DocumentReviewRecord.model_validate_json(
                row["consultation_review_json"]
            )
            if row["consultation_review_json"]
            else None,
            act_application_review=DocumentReviewRecord.model_validate_json(
                row["act_application_review_json"]
            )
            if row["act_application_review_json"]
            else None,
            review_work=tuple(
                ReviewWork.model_validate(value)
                for value in json.loads(row["review_work_json"] or "[]")
            ),
            observed_process=(
                ObservedProcess.model_validate_json(row["observed_process_json"])
                if row["observed_process_json"]
                else None
            ),
            analysis=analysis,
            editorial=(
                Editorial.model_validate_json(row["editorial_json"])
                if row["editorial_json"]
                else Editorial()
            ),
            analysis_attempts=int(row["analysis_attempts"]),
            analysis_generation=int(row["analysis_generation"]),
            awaiting_batch_since=_parse_dt(row["awaiting_batch_since"]),
            ready_analysis=(
                ReadyAnalysis.model_validate_json(row["ready_analysis_json"])
                if row["ready_analysis_json"]
                else None
            ),
            last_error=row["last_error"],
            submission=(
                BillSubmission.model_validate_json(row["submission_json"])
                if row["submission_json"]
                else None
            ),
            linked_number=row["linked_number"],
            linked_wykaz_number=row["linked_wykaz_number"],
            act=ActInfo.model_validate_json(row["act_json"]) if row["act_json"] else None,
            authors=(
                BillAuthors.model_validate_json(row["authors_json"])
                if row["authors_json"]
                else None
            ),
            agenda=tuple(
                AgendaItem.model_validate(i) for i in json.loads(row["agenda_json"] or "[]")
            ),
            rcl=RclProject.model_validate_json(row["rcl_json"]) if row["rcl_json"] else None,
            wykaz=(
                WykazEntry.model_validate_json(row["wykaz_json"]) if row["wykaz_json"] else None
            ),
            senate=(
                SenateAct.model_validate_json(row["senate_json"]) if row["senate_json"] else None
            ),
            seen_supplements=(
                tuple(json.loads(row["supplements_json"])) if row["supplements_json"] else None
            ),
            joint=(
                JointRecord.model_validate_json(row["joint_json"]) if row["joint_json"] else None
            ),
            observation_mode=(
                ObservationMode(row["observation_mode"]) if row["observation_mode"] else None
            ),
            observation_basis=(
                ObservationBasis(row["observation_basis"]) if row["observation_basis"] else None
            ),
            discontinued_at=(
                datetime.fromisoformat(row["discontinued_at"]) if row["discontinued_at"] else None
            ),
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            last_checked_at=datetime.fromisoformat(row["last_checked_at"]),
        )
        if bill.joint is not None:
            validate_secondary_dependency(self._conn, bill.joint, "joint")
        if bill.consultation_review is not None:
            validate_secondary_dependency(
                self._conn, bill.consultation_review, "consultation_review"
            )
        if bill.commission_review is not None:
            validate_secondary_dependency(self._conn, bill.commission_review, "commission_review")
        if bill.act_application_review is not None:
            validate_secondary_dependency(
                self._conn, bill.act_application_review, "act_application"
            )
        return bill

    def _row_to_status_change(self, row: sqlite3.Row) -> StatusChange:
        change = read_status_change(row)
        validate_supplement_dependencies(self._conn, change.supplements)
        if change.amendments is not None:
            validate_secondary_dependency(self._conn, change.amendments, "amendments")
        return change

    @staticmethod
    def _row_to_publication(row: sqlite3.Row) -> Publication:
        return Publication(
            attention=(
                AttentionAssessment.model_validate_json(row["attention_json"])
                if row["attention_json"]
                else None
            ),
            delivery=(
                DeliveryPlan.model_validate_json(row["delivery_json"])
                if row["delivery_json"]
                else None
            ),
            id=int(row["id"]),
            term=int(row["term"]),
            number=row["number"],
            kind=PublicationKind(row["kind"]),
            attempts=int(row["attempts"]),
            status=PublicationStatus(row["status"]),
            channel_id=row["channel_id"],
            ref=row["ref"],
            message_id=row["message_id"],
            telegram_chat_id=row["telegram_chat_id"],
            document_message_ids=json.loads(row["document_message_ids"] or "[]"),
            status_change_id=row["status_change_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            sent_at=datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None,
            error=row["error"],
            rendered_sha256=row["rendered_sha256"],
        )


def _bool(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _supplements_json(records: list[SupplementRecord]) -> str | None:
    if not records:
        return None
    return json.dumps([r.model_dump(mode="json") for r in records], ensure_ascii=False)


def _iso_date(value: object) -> str | None:
    return None if value is None else str(value)
