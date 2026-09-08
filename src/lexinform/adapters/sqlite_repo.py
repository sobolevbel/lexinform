"""SQLite implementation of BillRepository (stdlib sqlite3, no ORM).

Schema is versioned with PRAGMA user_version; `migrate()` applies missing steps in order.
`dump()` / `restore()` move the whole database through a text SQL script so the state can live in
a git branch with readable diffs.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from lexinform.models import (
    PRE_PRINT_PREFIX,
    ActInfo,
    AnalysisRecord,
    Bill,
    BillAuthors,
    BillStatus,
    BillSubmission,
    ProcessSummary,
    Publication,
    PublicationKind,
    PublicationStatus,
    RunReport,
    Stage,
    StatusChange,
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
)

SCHEMA_VERSION = len(MIGRATIONS)
_VERSION_LINE = "PRAGMA user_version = "


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dump_version(script: str) -> int:
    for line in reversed(script.splitlines()):
        if line.startswith(_VERSION_LINE):
            return int(line[len(_VERSION_LINE) :].rstrip(";").strip())
    return 1


class SqliteBillRepository:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)  # autocommit; explicit txns
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._in_txn = False

    # ------------------------------------------------------------------ schema / lifecycle

    def migrate(self) -> None:
        current = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
            self._conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")

    def close(self) -> None:
        self._conn.close()

    def begin(self) -> None:
        if not self._in_txn:
            self._conn.execute("BEGIN")
            self._in_txn = True

    def commit(self) -> None:
        if self._in_txn:
            self._conn.execute("COMMIT")
            self._in_txn = False

    def rollback(self) -> None:
        if self._in_txn:
            self._conn.execute("ROLLBACK")
            self._in_txn = False

    def dump(self) -> str:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        return "\n".join(self._conn.iterdump()) + f"\n{_VERSION_LINE}{version};\n"

    def restore(self, script: str) -> None:
        """Replace the current contents with a script produced by `dump()`."""
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

    # ------------------------------------------------------------------ bills

    def get(self, term: int, number: str) -> Bill | None:
        row = self._conn.execute(
            "SELECT * FROM bills WHERE term = ? AND number = ?", (term, number)
        ).fetchone()
        return self._row_to_bill(row) if row else None

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

    def set_status(
        self, term: int, number: str, status: BillStatus, *, prefilter_hits: list[str] | None = None
    ) -> None:
        if prefilter_hits is None:
            self._conn.execute(
                "UPDATE bills SET status = ? WHERE term = ? AND number = ?",
                (status.value, term, number),
            )
        else:
            self._conn.execute(
                "UPDATE bills SET status = ?, prefilter_hits = ? WHERE term = ? AND number = ?",
                (status.value, json.dumps(prefilter_hits, ensure_ascii=False), term, number),
            )

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

    def list_by_status(
        self,
        term: int,
        statuses: list[BillStatus],
        *,
        limit: int,
        max_attempts: int | None = None,
    ) -> list[Bill]:
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        attempts_clause = "AND analysis_attempts < ?" if max_attempts is not None else ""
        params: list[object] = [term, *[s.value for s in statuses]]
        if max_attempts is not None:
            params.append(max_attempts)
        params.append(limit)
        rows = self._conn.execute(
            f"""
            SELECT * FROM bills WHERE term = ? AND status IN ({placeholders}) {attempts_clause}
            ORDER BY change_date DESC LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def list_publish_candidates(
        self, term: int, channel_id: str, *, min_score: int, limit: int, max_attempts: int = 3
    ) -> list[Bill]:
        rows = self._conn.execute(
            """
            SELECT b.* FROM bills b
            WHERE b.term = ? AND b.status = ? AND b.analysis_json IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM publications p
                  WHERE p.term = b.term AND p.number = b.number AND p.kind = 'new_bill'
                    AND p.channel_id = ?
                    AND (p.status IN ('sent', 'skipped', 'pending', 'unknown')
                         OR (p.status = 'failed' AND p.attempts >= ?))
              )
            """,
            (term, BillStatus.ANALYZED.value, channel_id, max_attempts),
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
        term: int,
        channel_id: str,
        *,
        closed_grace_days: int,
        passed_max_days: int,
        now: datetime,
        changed_since: datetime | None = None,
    ) -> list[Bill]:
        """Published bills still worth polling.

        Closed bills are followed for `closed_grace_days`; bills passed by the Sejm whose act has
        not appeared in Dziennik Ustaw yet are followed longer (`passed_max_days`), because the
        Senate, the President and publication take weeks.

        With `changed_since`, only bills whose `change_date` (refreshed by discovery from the
        API's `modifiedSince` listing) is at least that recent are returned, plus bills passed
        by the Sejm that still wait for their act: the ELI address may appear without a visible
        change of the process.
        """
        cutoff = (now - timedelta(days=closed_grace_days)).date().isoformat()
        passed_cutoff = (now - timedelta(days=passed_max_days)).date().isoformat()
        sql = """
            SELECT b.* FROM bills b
            JOIN publications p ON p.term = b.term AND p.number = b.number
            WHERE b.term = ? AND p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?
              AND b.status != ?
              AND (b.closure_date IS NULL OR b.closure_date >= ?
                   OR (b.passed = 1 AND b.act_json IS NULL AND b.closure_date >= ?))
            """
        params: list[object] = [term, channel_id, BillStatus.LINKED.value, cutoff, passed_cutoff]
        if changed_since is not None:
            # Timestamps are stored as ISO text in UTC; compare to the second.
            sql += """
              AND (substr(b.change_date, 1, 19) >= substr(?, 1, 19)
                   OR (b.passed = 1 AND b.act_json IS NULL))
            """
            since = changed_since if changed_since.tzinfo is None else changed_since.astimezone(UTC)
            params.append(since.replace(tzinfo=None).isoformat())
        rows = self._conn.execute(sql + " ORDER BY b.number", params).fetchall()
        return [self._row_to_bill(r) for r in rows]

    # ------------------------------------------------------------------ published acts

    def save_authors(self, term: int, number: str, authors: BillAuthors) -> None:
        self._conn.execute(
            "UPDATE bills SET authors_json = ? WHERE term = ? AND number = ?",
            (authors.model_dump_json(), term, number),
        )

    def save_act(self, term: int, number: str, act: ActInfo) -> None:
        self._conn.execute(
            "UPDATE bills SET act_json = ?, entry_into_force = ? WHERE term = ? AND number = ?",
            (act.model_dump_json(), _iso_date(act.entry_into_force), term, number),
        )

    def list_due_in_force(self, term: int, channel_id: str, *, today: date) -> list[Bill]:
        """Published bills whose act enters into force today or earlier, not yet reminded."""
        rows = self._conn.execute(
            """
            SELECT b.* FROM bills b
            JOIN publications p ON p.term = b.term AND p.number = b.number
            WHERE b.term = ? AND p.kind = 'new_bill' AND p.status = 'sent' AND p.channel_id = ?
              AND b.entry_into_force IS NOT NULL AND b.entry_into_force <= ?
              AND NOT EXISTS (
                  SELECT 1 FROM publications r
                  WHERE r.term = b.term AND r.number = b.number AND r.kind = 'in_force'
                    AND r.channel_id = ?
              )
            ORDER BY b.entry_into_force, b.number
            """,
            (term, channel_id, today.isoformat(), channel_id),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    # ------------------------------------------------------------------ pre-print bills

    def save_submission(self, term: int, number: str, submission: BillSubmission) -> None:
        self._conn.execute(
            "UPDATE bills SET submission_json = ? WHERE term = ? AND number = ?",
            (submission.model_dump_json(), term, number),
        )

    def list_pre_print(self, term: int) -> list[Bill]:
        rows = self._conn.execute(
            "SELECT * FROM bills WHERE term = ? AND number LIKE ? AND status != ?"
            " ORDER BY change_date",
            (term, f"{PRE_PRINT_PREFIX}%", BillStatus.LINKED.value),
        ).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def link_bills(self, term: int, pre_print_number: str, print_number: str) -> None:
        self._conn.execute(
            "UPDATE bills SET linked_number = ?, status = ? WHERE term = ? AND number = ?",
            (print_number, BillStatus.LINKED.value, term, pre_print_number),
        )
        self._conn.execute(
            "UPDATE bills SET linked_number = ? WHERE term = ? AND number = ?",
            (pre_print_number, term, print_number),
        )

    # ------------------------------------------------------------------ publications

    def create_publication(self, publication: Publication) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO publications (term, number, kind, status, channel_id, message_id,
                document_message_ids, status_change_id, created_at, sent_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET status = excluded.status, created_at = excluded.created_at,
                                      error = NULL
            """,
            (
                publication.term,
                publication.number,
                publication.kind.value,
                publication.status.value,
                publication.channel_id,
                publication.message_id,
                json.dumps(publication.document_message_ids),
                publication.status_change_id,
                publication.created_at.isoformat(),
                _iso(publication.sent_at),
                publication.error,
            ),
        )
        # lastrowid is unreliable after an upsert that took the UPDATE path, so look the row up
        # by its natural key instead.
        del cur
        if publication.kind is PublicationKind.STATUS_UPDATE:
            row = self._conn.execute(
                "SELECT id FROM publications WHERE status_change_id = ? AND channel_id = ?"
                " AND kind = 'status_update'",
                (publication.status_change_id, publication.channel_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id FROM publications WHERE term = ? AND number = ? AND channel_id = ?"
                " AND kind = ?",
                (
                    publication.term,
                    publication.number,
                    publication.channel_id,
                    publication.kind.value,
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
                int(status is PublicationStatus.FAILED),
                publication_id,
            ),
        )

    def get_publication(
        self, term: int, number: str, kind: str, channel_id: str
    ) -> Publication | None:
        row = self._conn.execute(
            """
            SELECT * FROM publications WHERE term = ? AND number = ? AND kind = ? AND channel_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (term, number, kind, channel_id),
        ).fetchone()
        return self._row_to_publication(row) if row else None

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

    # ------------------------------------------------------------------ status changes

    def add_status_change(self, change: StatusChange) -> int | None:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO status_changes (term, number, old_fingerprint, new_fingerprint,
                                            new_stages_json, closure_detected, passed,
                                            content_changed, withdrawn, detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    change.detected_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid or 0)

    def closure_announced(self, term: int, number: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM status_changes WHERE term = ? AND number = ? AND closure_detected = 1"
            " LIMIT 1",
            (term, number),
        ).fetchone()
        return row is not None

    def list_failed_status_changes(
        self, term: int, channel_id: str, *, max_attempts: int
    ) -> list[StatusChange]:
        """Status changes whose update post failed and may be retried."""
        rows = self._conn.execute(
            """
            SELECT c.* FROM status_changes c
            JOIN publications p ON p.status_change_id = c.id AND p.kind = 'status_update'
            WHERE c.term = ? AND p.channel_id = ? AND p.status = 'failed' AND p.attempts < ?
            ORDER BY c.id
            """,
            (term, channel_id, max_attempts),
        ).fetchall()
        return [self._row_to_status_change(r) for r in rows]

    # ------------------------------------------------------------------ runs

    def last_discovery_started_at(self) -> datetime | None:
        """Start of the last full run whose discovery phase completed (the watermark)."""
        row = self._conn.execute(
            "SELECT started_at FROM runs WHERE discovery_ok = 1 AND mode = 'run'"
            " ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def start_run(self, report: RunReport) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (started_at, since, mode) VALUES (?, ?, ?)",
            (report.started_at.isoformat(), report.since.isoformat(), report.mode),
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

    # ------------------------------------------------------------------ row mapping

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
            act=ActInfo.model_validate_json(row["act_json"]) if row["act_json"] else None,
            authors=(
                BillAuthors.model_validate_json(row["authors_json"])
                if row["authors_json"]
                else None
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
            message_id=row["message_id"],
            document_message_ids=json.loads(row["document_message_ids"] or "[]"),
            status_change_id=row["status_change_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            sent_at=datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None,
            error=row["error"],
        )


def _bool(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _iso_date(value: object) -> str | None:
    return None if value is None else str(value)
