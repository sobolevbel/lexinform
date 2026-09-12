"""SQLite implementation of BillRepository (stdlib sqlite3, no ORM).

Schema is versioned with PRAGMA user_version; `migrate()` applies missing steps in order.
`dump()` / `restore()` move the whole database through a text SQL script so the state can live in
a git branch with readable diffs.
"""

import json
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from lexinform.models import (
    PRE_PRINT_PREFIX,
    RCL_PREFIX,
    WYKAZ_PREFIX,
    ActInfo,
    AgendaItem,
    AmendmentsRecord,
    AnalysisRecord,
    Bill,
    BillAuthors,
    BillStatus,
    BillSubmission,
    CommandState,
    IncomingCommand,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    RclProject,
    RunMode,
    RunReport,
    Stage,
    StatusChange,
    WykazEntry,
)

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
)

SCHEMA_VERSION = len(MIGRATIONS)
_VERSION_LINE = "PRAGMA user_version = "


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


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
                    summary.model_dump_json(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        else:
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
                    summary.model_dump_json(),
                    now.isoformat(),
                    summary.term,
                    summary.number,
                ),
            )
        bill = self.get(summary.term, summary.number)
        assert bill is not None
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
    ) -> None:
        hits = None
        if prefilter_hits is not None:
            hits = json.dumps(prefilter_hits, ensure_ascii=False)
        self._conn.execute(
            """
            UPDATE bills SET status = ?,
                prefilter_hits = COALESCE(?, prefilter_hits),
                last_error = COALESCE(?, last_error)
            WHERE term = ? AND number = ?
            """,
            (status.value, hits, reason, term, number),
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
                json.dumps([s.model_dump(mode="json") for s in stages], ensure_ascii=False),
                fingerprint,
                term,
                number,
            ),
        )

    def save_analysis(self, term: int, number: str, record: AnalysisRecord) -> None:
        self._conn.execute(
            """
            UPDATE bills SET analysis_json = ?, status = ?, last_error = NULL
            WHERE term = ? AND number = ?
            """,
            (record.model_dump_json(), BillStatus.ANALYZED.value, term, number),
        )

    def record_analysis_failure(self, term: int, number: str, error: str) -> None:
        self._conn.execute(
            """
            UPDATE bills SET status = ?, analysis_attempts = analysis_attempts + 1, last_error = ?
            WHERE term = ? AND number = ?
            """,
            (BillStatus.ANALYSIS_FAILED.value, error[:2000], term, number),
        )

    def reset_bill(
        self, term: int, number: str, status: BillStatus, *, reason: str | None = None
    ) -> None:
        """Operator action: put a bill back into `status` with a clean retry budget. `reason`
        replaces the last error (a bill silenced by `/skip` says so instead of keeping the
        prefilter's silence, which reads as a keyword miss)."""
        self._conn.execute(
            "UPDATE bills SET status = ?, analysis_attempts = 0, last_error = ?"
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

    def list_publish_candidates(
        self, channel_id: str, *, min_score: int, limit: int, max_attempts: int = 3
    ) -> list[Bill]:
        """Analysed bills that still need a post. A card or an "alternative bill" reply settles
        the bill; a failed one leaves it listed until the attempts are used up."""
        rows = self._conn.execute(
            """
            SELECT b.* FROM bills b
            WHERE b.status = ? AND b.analysis_json IS NOT NULL
              AND b.discontinued_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM publications p
                  WHERE p.term = b.term AND p.number = b.number
                    AND p.kind IN ('new_bill', 'joint_bill')
                    AND p.channel_id = ?
                    AND (p.status IN ('sent', 'skipped', 'pending', 'unknown')
                         OR (p.status = 'failed' AND p.attempts >= ?))
              )
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

    def list_tracked(
        self,
        channel_id: str,
        *,
        closed_grace_days: int,
        passed_max_days: int,
        pending_decision_max_days: int,
        now: datetime,
        changed_since: datetime | None = None,
    ) -> list[Bill]:
        """Published bills still worth polling, whichever term they belong to.

        Closed bills are followed for `closed_grace_days`; bills passed by the Sejm whose act has
        not appeared in Dziennik Ustaw yet are followed longer (`passed_max_days`), because the
        Senate, the President and publication take weeks. A bill waiting for the Sejm to answer a
        veto or for the Constitutional Tribunal is followed for `pending_decision_max_days`: every
        window counts from `closure_date`, which the Sejm sets at the third reading, long before
        either of those begins. An act whose entry into force ELI has not indexed yet is kept
        whatever its age, because only a later fetch can fill that date in.

        With `changed_since`, only bills whose `change_date` (refreshed by discovery from the
        API's `modifiedSince` listing) is at least that recent are returned, plus bills passed
        by the Sejm that still wait for their act: the ELI address may appear without a visible
        change of the process.
        """
        cutoff = (now - timedelta(days=closed_grace_days)).date().isoformat()
        passed_cutoff = (now - timedelta(days=passed_max_days)).date().isoformat()
        decision_cutoff = (now - timedelta(days=pending_decision_max_days)).date().isoformat()
        sql = f"""
            SELECT b.* FROM bills b
            JOIN publications p ON p.term = b.term AND p.number = b.number
            WHERE p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?
              AND b.status != ? AND b.discontinued_at IS NULL
              AND (b.closure_date IS NULL OR b.closure_date >= ?
                   OR (b.passed = 1 AND b.act_json IS NULL AND b.closure_date >= ?)
                   OR (b.act_json IS NOT NULL AND b.entry_into_force IS NULL)
                   OR (b.act_json IS NULL AND b.closure_date >= ? AND {self._AWAITS_DECISION}))
            """
        params: list[object] = [
            channel_id,
            BillStatus.LINKED.value,
            cutoff,
            passed_cutoff,
            decision_cutoff,
        ]
        if changed_since is not None:
            # Timestamps are stored as ISO text in UTC; compare to the second. A wykaz row is
            # exempt: its `change_date` is the register's `Data publikacji`, which never moves,
            # and the whole register is downloaded every run anyway.
            sql += """
              AND (substr(b.change_date, 1, 19) >= substr(?, 1, 19)
                   OR (b.passed = 1 AND b.act_json IS NULL)
                   OR b.number LIKE ?)
            """
            since = changed_since if changed_since.tzinfo is None else changed_since.astimezone(UTC)
            params.extend([since.replace(tzinfo=None).isoformat(), f"{WYKAZ_PREFIX}%"])
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
    # The President sent the law back to the Sejm or to the Tribunal: the answer can take years,
    # and until it comes the bill still has an act ahead of it.
    _AWAITS_DECISION = """
        EXISTS (
            SELECT 1 FROM json_tree(b.stages_json)
            WHERE json_tree.key = 'stage_type'
              AND json_tree.value IN ('Veto', 'PresidentToTribunal')
        )
    """
    # A one-off post blocks its bill once it is sent, skipped, pending or unknown; a failed one
    # leaves the bill listed so the poster can retry it within the attempt budget.
    _NO_SETTLED_POST = """
        AND NOT EXISTS (
            SELECT 1 FROM publications r
            WHERE r.term = b.term AND r.number = b.number AND r.kind = ? AND r.channel_id = ?
              AND r.status IN ('sent', 'skipped', 'pending', 'unknown')
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
            (project.model_dump_json(), term, number),
        )

    def list_rcl_awaiting_link(self) -> list[Bill]:
        rows = self._conn.execute(
            "SELECT * FROM bills WHERE number LIKE ? AND status != ?"
            " AND json_extract(rcl_json, '$.print_number') IS NOT NULL ORDER BY term, number",
            (f"{RCL_PREFIX}%", BillStatus.LINKED.value),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def find_rcl(self, number: str) -> Bill | None:
        # RCL ids never repeat, and a project row lives in one term at a time (see
        # `move_rcl_projects`), so the number alone identifies the row.
        row = self._conn.execute(
            "SELECT * FROM bills WHERE number = ? AND number LIKE ? ORDER BY term DESC LIMIT 1",
            (number, f"{RCL_PREFIX}%"),
        ).fetchone()
        return self._row_to_bill(row) if row else None

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
                "SELECT number FROM bills WHERE term = ? AND status != ?"
                " AND ((number LIKE ? AND json_extract(rcl_json, '$.print_number') IS NULL)"
                "      OR number LIKE ?) ORDER BY number",
                (from_term, BillStatus.LINKED.value, f"{RCL_PREFIX}%", f"{WYKAZ_PREFIX}%"),
            )
        ]
        if not numbers:
            return 0
        placeholders = ",".join("?" for _ in numbers)
        # A savepoint works in autocommit mode and inside a dry run's open transaction alike.
        self._conn.execute("SAVEPOINT rehome")
        try:
            for table in ("publications", "status_changes"):
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
        self._conn.execute(
            "UPDATE bills SET linked_number = ?, status = ? WHERE term = ? AND number = ?",
            (print_number, BillStatus.LINKED.value, term, pre_print_number),
        )
        self._conn.execute(
            "UPDATE bills SET linked_number = ?, linked_wykaz_number = ?"
            " WHERE term = ? AND number = ?",
            (pre_print_number, wykaz_number, term, print_number),
        )

    def create_publication(self, publication: Publication) -> int:
        """Insert the row or, when its unique key exists, reset that row to the given status.

        Returns the row id, looked up by the natural key: `lastrowid` is unreliable after an
        upsert that took the UPDATE path.
        """
        self._conn.execute(
            """
            INSERT INTO publications (term, number, kind, status, channel_id, ref, message_id,
                document_message_ids, status_change_id, created_at, sent_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET status = excluded.status, created_at = excluded.created_at,
                                      error = NULL
            """,
            (
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
        assert row is not None
        return int(row[0])

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

    def set_card_digest(self, publication_id: int, digest: str) -> None:
        """Remember what the card said when it was last sent or edited."""
        self._conn.execute(
            "UPDATE publications SET rendered_sha256 = ? WHERE id = ?", (digest, publication_id)
        )

    def mark_stale_pending_as_unknown(self, *, now: datetime) -> int:
        cur = self._conn.execute(
            "UPDATE publications SET status = ?, error = ? WHERE status = ?",
            (
                PublicationStatus.UNKNOWN.value,
                f"left pending by a crashed run; marked unknown at {now.isoformat()}",
                PublicationStatus.PENDING.value,
            ),
        )
        return int(cur.rowcount or 0)

    def add_status_change(self, change: StatusChange) -> int | None:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO status_changes (term, number, old_fingerprint, new_fingerprint,
                                            new_stages_json, closure_detected, passed,
                                            content_changed, withdrawn, discontinued,
                                            amendments_json, detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    change.detected_at.isoformat(),
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
            ORDER BY c.id
            """,
            (term, number, channel_id),
        ).fetchall()
        return [self._row_to_status_change(r) for r in rows]

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

    def list_failed_status_changes(
        self, channel_id: str, *, max_attempts: int
    ) -> list[StatusChange]:
        """Status changes whose update post failed and may be retried."""
        rows = self._conn.execute(
            """
            SELECT c.* FROM status_changes c
            JOIN publications p ON p.status_change_id = c.id AND p.kind = 'status_update'
            WHERE p.channel_id = ? AND p.status = 'failed' AND p.attempts < ?
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
        cur = self._conn.execute("DELETE FROM runs WHERE started_at < ?", (before.isoformat(),))
        return int(cur.rowcount or 0)

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
            analysis=analysis,
            analysis_attempts=int(row["analysis_attempts"]),
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
            number=row["number"],
            old_fingerprint=row["old_fingerprint"],
            new_fingerprint=row["new_fingerprint"],
            new_stages=[Stage.model_validate(s) for s in json.loads(row["new_stages_json"])],
            closure_detected=bool(row["closure_detected"]),
            passed=None if row["passed"] is None else bool(row["passed"]),
            content_changed=bool(row["content_changed"]),
            withdrawn=bool(row["withdrawn"]),
            discontinued=bool(row["discontinued"]),
            amendments=(
                AmendmentsRecord.model_validate_json(row["amendments_json"])
                if row["amendments_json"]
                else None
            ),
            detected_at=datetime.fromisoformat(row["detected_at"]),
        )

    @staticmethod
    def _row_to_publication(row: sqlite3.Row) -> Publication:
        return Publication(
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


def _iso_date(value: object) -> str | None:
    return None if value is None else str(value)
