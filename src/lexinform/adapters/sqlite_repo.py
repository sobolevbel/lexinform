"""SQLite implementation of BillRepository (stdlib sqlite3, no ORM).

Schema is versioned with PRAGMA user_version; `migrate()` applies missing steps in order.
`dump()` / `restore()` move the whole database through a text SQL script so the state can live in
a git branch with readable diffs.
"""

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from lexinform.json_storage import compact_json, storage_json
from lexinform.legacy_delivery import legacy_status_edit
from lexinform.models import (
    PRE_PRINT_PREFIX,
    RCL_PREFIX,
    WYKAZ_PREFIX,
    ActInfo,
    AgendaItem,
    AmendmentsRecord,
    AnalysisRecord,
    AttentionAssessment,
    BatchIntent,
    BatchRequest,
    BatchResult,
    BatchStatus,
    Bill,
    BillAuthors,
    BillStatus,
    BillSubmission,
    CheckAspect,
    CommandState,
    DeliveryAlert,
    DeliveryPlan,
    DeliveryResolution,
    DocumentReviewRecord,
    DocumentWork,
    Editorial,
    IncomingCommand,
    JointRecord,
    LlmBatch,
    LlmBatchItem,
    Mnemonic,
    ObservationBasis,
    ObservationMode,
    ObservedProcess,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    RclProject,
    ReadyAnalysis,
    ReviewSource,
    ReviewWork,
    RunMode,
    RunReport,
    SenateAct,
    SourceCheck,
    Stage,
    StatusChange,
    SupplementRecord,
    WykazEntry,
    card_edit_owner_id,
    failed_card_part_resolution_allowed,
    rcl_num_key,
    valid_mnemonic,
)
from lexinform.models.batch import BatchJob, batch_item_meta
from lexinform.telegram_text import MESSAGE_LIMIT, utf16_length

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
)

SCHEMA_VERSION = len(MIGRATIONS)
_VERSION_LINE = "PRAGMA user_version = "


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


def _row_to_llm_batch_item(row: sqlite3.Row) -> LlmBatchItem:
    return LlmBatchItem(
        batch_id=row["batch_id"],
        custom_id=row["custom_id"],
        call_kind=row["call_kind"],
        term=row["term"],
        number=row["number"],
        meta=batch_item_meta(row["call_kind"], row["meta_json"]),
        consumed_at=_parse_dt(row["consumed_at"]),
        result=BatchResult.model_validate_json(row["result_json"]) if row["result_json"] else None,
    )


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


class SqliteBillRepository:
    """`BillRepository` on one sqlite3 connection; JSON columns hold pydantic dumps."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)  # autocommit, see begin()
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._in_txn = False

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
            for number, migration in enumerate(MIGRATIONS[version:], start=version + 1):
                scratch.executescript(f"BEGIN;{migration}PRAGMA user_version = {number};COMMIT;")
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
            "SELECT analysis_json, status FROM bills WHERE term = ? AND number = ?",
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
                record.model_dump_json(),
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
            self.save_editorial(term, number, bill.editorial.select(candidate))

    def save_editorial(self, term: int, number: str, editorial: Editorial) -> None:
        self._conn.execute(
            "UPDATE bills SET editorial_json = ? WHERE term = ? AND number = ?",
            (editorial.model_dump_json(), term, number),
        )

    def save_ready_analysis(self, term: int, number: str, ready: ReadyAnalysis) -> None:
        self._conn.execute(
            "UPDATE bills SET ready_analysis_json = ? WHERE term = ? AND number = ?",
            (ready.model_dump_json(), term, number),
        )

    def save_observed_process(self, term: int, number: str, observed: ObservedProcess) -> None:
        self._conn.execute(
            "UPDATE bills SET observed_process_json = ? WHERE term = ? AND number = ?",
            (storage_json(observed), term, number),
        )

    def load_analysis_memo(self) -> dict[str, str]:
        return dict(self._conn.execute("SELECT key, record_json FROM analysis_memo"))

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
        column = {
            "consultation_review": "consultation_review_json",
            "commission_review": "commission_review_json",
            "act_application": "act_application_review_json",
        }[review.review.purpose]
        self._conn.execute(
            f"UPDATE bills SET {column} = ? WHERE term = ? AND number = ?",
            (review.model_dump_json(), term, number),
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
        self._conn.execute(
            "INSERT INTO analysis_memo(key, record_json, term, number, last_used_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
            " term = COALESCE(excluded.term, analysis_memo.term),"
            " number = COALESCE(excluded.number, analysis_memo.number),"
            " last_used_at = COALESCE(excluded.last_used_at, analysis_memo.last_used_at)",
            (key, record_json, term, number, _iso(used_at)),
        )

    def save_update_delivery(self, change_id: int, channel_id: str, delivery: DeliveryPlan) -> None:
        self._conn.execute(
            "UPDATE publications SET delivery_json = ?, attention_json = ?"
            " WHERE status_change_id = ?"
            " AND channel_id = ? AND kind = 'status_update' AND delivery_json IS NULL",
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
        cur = self._conn.execute(
            "UPDATE publications SET delivery_json = ?, attention_json = ?"
            " WHERE status_change_id = ?"
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

    def freeze_legacy_status_edit(self, publication_id: int, delivery: DeliveryPlan) -> bool:
        row = self._conn.execute(
            "SELECT * FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
        if row is None:
            return False
        original = self._row_to_publication(row)
        if (
            not legacy_status_edit(original)
            or not delivery.rendered_text.strip()
            or utf16_length(delivery.rendered_text) > MESSAGE_LIMIT
            or delivery.render_origin != "legacy_snapshot"
            or self.digest_owner(publication_id) is not None
            or self.digest_sources(publication_id)
        ):
            return False
        saved = original.delivery
        assert saved is not None, "legacy_status_edit requires a saved semantic edit snapshot"
        if delivery != saved.model_copy(
            update={"rendered_text": delivery.rendered_text, "render_origin": "legacy_snapshot"}
        ):
            return False
        raw: dict[str, object] = json.loads(row["delivery_json"])
        raw.update(rendered_text=delivery.rendered_text, render_origin="legacy_snapshot")
        changed = self._conn.execute(
            "UPDATE publications SET delivery_json = ?"
            " WHERE id = ? AND status = 'failed' AND attempts = ? AND delivery_json = ?",
            (
                json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
                publication_id,
                original.attempts,
                row["delivery_json"],
            ),
        )
        return changed.rowcount == 1

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
            " AND json_extract(delivery_json, '$.revision_of') = ? ORDER BY id DESC",
            (publication_id,),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

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

    def save_batch_intent(self, intent: BatchIntent) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO llm_batch_intents"
            " (custom_id, provider, request_json, meta_json, state, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                intent.request.custom_id,
                intent.provider,
                intent.request.model_dump_json(),
                intent.meta.model_dump_json(),
                intent.state,
                intent.created_at.isoformat(),
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
        return [
            BatchIntent(
                request=(request := BatchRequest.model_validate_json(row["request_json"])),
                meta=batch_item_meta(request.call_kind, row["meta_json"]),
                provider=row["provider"],
                state=row["state"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

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
                (i.batch_id, i.custom_id, i.call_kind, i.term, i.number, i.meta.model_dump_json())
                for i in items
            ],
        )

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
        return [_row_to_llm_batch_item(r) for r in rows]

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
            _row_to_llm_batch_item(row)
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
        self._conn.execute(
            "UPDATE bills SET joint_json = ? WHERE term = ? AND number = ?",
            (record.model_dump_json(), term, number),
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

    def move_government_rows(self, from_term: int, to_term: int) -> int:
        numbers = [
            str(r[0])
            for r in self._conn.execute(
                "SELECT number FROM bills WHERE term = ? AND status NOT IN (?, ?, ?, ?)"
                " AND ((number LIKE ? AND json_extract(rcl_json, '$.print_number') IS NULL)"
                "      OR number LIKE ?) ORDER BY number",
                (
                    from_term,
                    BillStatus.LINKED.value,
                    BillStatus.BATCH_PENDING.value,
                    BillStatus.ANALYSIS_READY.value,
                    BillStatus.REANALYSIS_READY.value,
                    f"{RCL_PREFIX}%",
                    f"{WYKAZ_PREFIX}%",
                ),
            )
        ]
        if not numbers:
            return 0
        placeholders = ",".join("?" for _ in numbers)
        # A savepoint works in autocommit mode and inside a dry run's open transaction alike.
        self._conn.execute("SAVEPOINT rehome")
        try:
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
        except Exception:
            self._conn.execute("ROLLBACK TO rehome")
            self._conn.execute("RELEASE rehome")
            raise
        self._conn.execute("RELEASE rehome")
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

    def move_batch_work(self, term: int, from_number: str, to_number: str) -> None:
        self._conn.execute(
            "UPDATE llm_batch_items SET number = ?"
            " WHERE term = ? AND number = ? AND consumed_at IS NULL",
            (to_number, term, from_number),
        )
        self._conn.execute(
            "UPDATE llm_batch_intents SET request_json = json_set(request_json, '$.number', ?)"
            " WHERE json_extract(request_json, '$.term') = ?"
            " AND json_extract(request_json, '$.number') = ?",
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

    def create_publication(self, publication: Publication) -> int:
        """Insert the row or, when its unique key exists, reset that row to the given status.

        Returns the row id, looked up by the natural key: `lastrowid` is unreliable after an
        upsert that took the UPDATE path.
        """
        attention = publication.attention or (
            publication.delivery.attention if publication.delivery else None
        )
        # Operator commands must never address a different message after its row was deleted.
        identity = self._conn.execute(
            "UPDATE publication_sequence SET last_id = last_id + 1 WHERE id = 1 RETURNING last_id"
        ).fetchone()
        assert identity is not None
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
            """,
            (
                identity[0],
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

    def complete_digest_events(
        self, owner_id: int, *, message_id: int, sent_at: datetime
    ) -> list[Publication]:
        owner = self.publication_by_id(owner_id)
        sources = self.digest_sources(owner_id)
        if (
            owner is None
            or owner.delivery is None
            or set(owner.delivery.digest_event_ids) != {source.id for source in sources}
        ):
            raise ValueError("digest membership does not match its saved payload")
        for source in sources:
            if (
                source.status is not PublicationStatus.QUEUED
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
            " AND (status = 'queued' OR (status = 'failed' AND attempts < ?)) ORDER BY id",
            (channel_id, max_attempts),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

    def publication_by_id(self, publication_id: int) -> Publication | None:
        row = self._conn.execute(
            "SELECT * FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
        return self._row_to_publication(row) if row else None

    def resolve_delivery(self, resolution: DeliveryResolution) -> bool:
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
            if original is None:
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
                member.status is not PublicationStatus.QUEUED
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
        return [DeliveryResolution.model_validate_json(row[0]) for row in rows]

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
        root = self._conn.execute(
            "SELECT ref FROM publications WHERE kind = 'message_part' AND ref LIKE ?"
            " ORDER BY id DESC LIMIT 1",
            (f"card/{card_id}/%/1",),
        ).fetchone()
        if root is None:
            card = self.publication_by_id(card_id)
            rows = self._conn.execute(
                "SELECT * FROM publications WHERE kind = 'message_part' AND ref LIKE ? ORDER BY id",
                (f"message/{card_id}/%",),
            ).fetchall()
            return ([card] if card is not None else []) + [
                self._row_to_publication(row) for row in rows
            ]
        prefix = root["ref"].rsplit("/", 1)[0]
        rows = self._conn.execute(
            "SELECT * FROM publications WHERE kind = 'message_part' AND ref LIKE ? ORDER BY id",
            (f"{prefix}/%",),
        ).fetchall()
        return [self._row_to_publication(row) for row in rows]

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
        self._conn.execute(
            "UPDATE status_changes SET amendments_json = ? WHERE id = ?",
            (record.model_dump_json(), change_id),
        )

    def save_status_change_supplements(
        self, change_id: int, records: list[SupplementRecord]
    ) -> None:
        self._conn.execute(
            "UPDATE status_changes SET supplements_json = ? WHERE id = ?",
            (_supplements_json(records), change_id),
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
            "SELECT work_json FROM document_work WHERE state='waiting' AND next_at <= ? "
            "ORDER BY next_at, key",
            (_utc_iso(due),),
        ).fetchall()
        return [DocumentWork.model_validate_json(row["work_json"]) for row in rows]

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

    def prune_history(self, *, before: datetime, memo_before: datetime) -> dict[str, int]:
        with self.atomic():
            publications = self._conn.execute(
                "UPDATE publications SET delivery_json = NULL WHERE status = 'sent'"
                " AND kind != 'amendments_revision'"
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
            ).rowcount
            memos = self._conn.execute(
                """
                DELETE FROM analysis_memo AS m WHERE last_used_at < ?
                  AND NOT EXISTS (SELECT 1 FROM document_work w
                    WHERE w.term=m.term AND w.number=m.number AND w.state != 'complete')
                  AND EXISTS (SELECT 1 FROM bills b WHERE b.term = m.term AND b.number = m.number
                    AND b.status IN ('analyzed', 'linked', 'skipped_prefilter',
                                     'skipped_text_prefilter', 'skipped_closed')
                    AND b.awaiting_batch_since IS NULL AND b.ready_analysis_json IS NULL
                    AND b.review_work_json IS NULL)
                  AND NOT EXISTS (SELECT 1 FROM publications p
                    WHERE p.term = m.term AND p.number = m.number
                      AND p.status NOT IN ('sent', 'skipped'))
                  AND NOT EXISTS (SELECT 1 FROM llm_batch_intents i
                    WHERE json_extract(i.request_json, '$.term') = m.term
                      AND json_extract(i.request_json, '$.number') = m.number)
                  AND NOT EXISTS (SELECT 1 FROM llm_batch_items i
                    WHERE i.term = m.term AND i.number = m.number
                      AND (i.consumed_at IS NULL OR i.accounted_at IS NULL))
                """,
                (_iso(memo_before),),
            ).rowcount
            items = self._conn.execute(
                """
                UPDATE llm_batch_items
                SET meta_json = json_remove(
                    meta_json, '$.text', '$.located', '$.review_context',
                    '$.explanation.text', '$.explanation.located'),
                  result_json = json_remove(result_json, '$.answer', '$.analysis')
                WHERE consumed_at IS NOT NULL AND accounted_at IS NOT NULL
                  AND batch_id IN (SELECT batch_id FROM llm_batches WHERE forgotten_at IS NOT NULL)
                  AND EXISTS (SELECT 1 FROM bills b
                    WHERE b.term = llm_batch_items.term AND b.number = llm_batch_items.number
                      AND b.status IN ('analyzed', 'linked', 'skipped_prefilter',
                                       'skipped_text_prefilter', 'skipped_closed')
                      AND b.awaiting_batch_since IS NULL AND b.ready_analysis_json IS NULL)
                  AND (length(json_extract(meta_json, '$.text')) > 0
                    OR json_type(meta_json, '$.located') = 'object'
                    OR json_type(meta_json, '$.review_context') = 'object'
                    OR length(json_extract(meta_json, '$.explanation.text')) > 0
                    OR json_type(result_json, '$.answer') = 'object'
                    OR json_type(result_json, '$.analysis') = 'object')
                """
            ).rowcount
        return {"delivery_payloads": publications, "analysis_memos": memos, "batch_payloads": items}

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
        rows = self._conn.execute(
            """
            SELECT * FROM bills
            WHERE json_extract(analysis_json, '$.input_tokens') IS NOT NULL
            ORDER BY json_extract(analysis_json, '$.input_tokens') DESC, term, number
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

    @staticmethod
    def _row_to_bill(row: sqlite3.Row) -> Bill:
        columns = row.keys()
        stages = tuple(Stage.model_validate(s) for s in json.loads(row["stages_json"] or "[]"))
        analysis = (
            AnalysisRecord.model_validate_json(row["analysis_json"])
            if row["analysis_json"]
            else None
        )
        return Bill(
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

    @staticmethod
    def _row_to_status_change(row: sqlite3.Row) -> StatusChange:
        return StatusChange(
            id=int(row["id"]),
            term=int(row["term"]),
            consultation_opened=bool(row["consultation_opened"]),
            number=row["number"],
            old_fingerprint=row["old_fingerprint"],
            new_fingerprint=row["new_fingerprint"],
            new_stages=[Stage.model_validate(s) for s in json.loads(row["new_stages_json"])],
            closure_detected=bool(row["closure_detected"]),
            decision_changed=bool(row["decision_changed"]),
            passed=None if row["passed"] is None else bool(row["passed"]),
            content_changed=bool(row["content_changed"]),
            withdrawn=bool(row["withdrawn"]),
            discontinued=bool(row["discontinued"]),
            amendments=(
                AmendmentsRecord.model_validate_json(row["amendments_json"])
                if row["amendments_json"]
                else None
            ),
            supplements=[
                SupplementRecord.model_validate(r)
                for r in json.loads(row["supplements_json"] or "[]")
            ],
            detected_at=datetime.fromisoformat(row["detected_at"]),
        )

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
