"""Read scoped document selection and observation identities from SQLite."""

import sqlite3
from datetime import datetime

from lexinform.models import DocumentSelectionRecord, SourceObservation
from lexinform.models.computations import ReviewInputReference


class DocumentSourceRows:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def _query(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        cursor = self._connection.cursor()
        cursor.row_factory = sqlite3.Row
        return cursor.execute(sql, parameters)

    def get_document_selection(self, selection_id: str) -> DocumentSelectionRecord | None:
        row = self._query(
            "SELECT * FROM document_selections WHERE selection_id=?", (selection_id,)
        ).fetchone()
        if row is None:
            return None
        record = DocumentSelectionRecord.model_validate_json(row["record_json"])
        expected = (
            record.selection_id,
            record.selection.question,
            record.selection.policy_version,
            record.selection.state,
        )
        if expected != tuple(
            row[key] for key in ("selection_id", "question", "policy_version", "state")
        ):
            raise ValueError("document selection columns differ from the recorded identity")
        return record

    def get_source_observation(self, observation_id: str) -> SourceObservation | None:
        row = self._query(
            "SELECT * FROM source_observations WHERE observation_id=?", (observation_id,)
        ).fetchone()
        return self._source_observation(row) if row else None

    def source_observations_for_selection(
        self, term: int, number: str, selection_id: str
    ) -> list[SourceObservation]:
        return [
            self._source_observation(row)
            for row in self._query(
                "SELECT * FROM source_observations INDEXED BY ix_source_observations_selection"
                " WHERE selection_id=? AND term=?"
                " AND number=? AND purpose='analysis' ORDER BY rowid LIMIT 2",
                (selection_id, term, number),
            )
        ]

    def review_source_observations(
        self, reference: ReviewInputReference, *, before: datetime
    ) -> tuple[tuple[int, str], ...]:
        confirmed = []
        for index, document in enumerate(reference.documents):
            identity = document.source_observation_id
            if identity is None:
                continue
            observation = self.get_source_observation(identity)
            if (
                observation is None
                or (observation.term, observation.number, observation.purpose)
                != (reference.term, reference.number, "review")
                or observation.document.url != document.url
                or observation.recorded_at > before
            ):
                raise ValueError("review_source_observation_conflict")
            record = self.get_document_selection(observation.selection_id)
            if record is None or not record.selection.matches_review_source(
                document.url, document.file_sha256, document.sha256
            ):
                raise ValueError("review_source_observation_conflict")
            confirmed.append((index, identity))
        return tuple(confirmed)

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
