"""Retain actual required-part exclusions without changing selection or source witnesses."""

import hashlib
import json
from collections.abc import Callable

from lexinform.models import (
    CandidateDispositionTrace,
    RequiredPartExclusion,
    RequiredPartExclusions,
)
from lexinform.models.candidate_trace import (
    MAX_CANDIDATE_TRACE_BYTES,
    validate_required_part_parent,
)
from lexinform.models.document_reads import LoadedFile
from lexinform.sections import Kind


class RequiredPartCapture:
    def __init__(self) -> None:
        self._rows: list[RequiredPartExclusion] | None = []
        self._count = 0
        self._bytes = 2
        self._digest = hashlib.sha256(b"[")
        self._failed = False
        self._last_key: tuple[bool, int, int] | None = None

    def fail(self) -> None:
        self._failed = True
        self._rows = None

    def observe(self, phase: str, index: int, position: int, kind: Kind, file: LoadedFile) -> None:
        if self._failed:
            return
        try:
            if file.text is None:
                raise ValueError("an exclusion requires its actually returned text")
            row = RequiredPartExclusion.model_validate(
                {
                    "phase": phase,
                    "index": index,
                    "required_index": position,
                    "reason": "required_non_annex_appendix",
                    "kind": kind,
                    "file_sha256": file.sha256,
                    "text_sha256": hashlib.sha256(file.text.encode()).hexdigest(),
                    "parts": tuple(
                        part.model_copy(update={"text": ""})
                        for part in (file.selection.observed_parts or file.selection.parts)
                    ),
                }
            )
            key = (row.phase == "fallback", row.index, row.required_index)
            if self._last_key is not None and key <= self._last_key:
                raise ValueError("required observations must retain actual unique call order")
            self._last_key = key
            encoded = json.dumps(
                row.model_dump(mode="json", exclude_defaults=True),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            self._digest.update(b"," if self._count else b"")
            self._digest.update(encoded)
            self._count += 1
            self._bytes += len(encoded) + int(self._count > 1)
            if self._bytes > MAX_CANDIDATE_TRACE_BYTES:
                self._rows = None
            elif self._rows is not None:
                self._rows.append(row)
        except Exception:
            self.fail()

    def enrich(
        self,
        original: CandidateDispositionTrace,
        archive_enricher: Callable[[CandidateDispositionTrace], CandidateDispositionTrace]
        | None = None,
    ) -> CandidateDispositionTrace:
        if self._failed or not self._count:
            return original
        try:
            if self._rows is not None and original.coverage == "complete":
                for row in self._rows:
                    validate_required_part_parent(original, row)
            digest = self._digest.copy()
            digest.update(b"]")
            summary = RequiredPartExclusions(
                retention="omitted",
                observed_rows=self._count,
                omitted_rows=self._count,
                rows_sha256=digest.hexdigest(),
                rows=(),
            )

            def retained(candidate: CandidateDispositionTrace) -> CandidateDispositionTrace:
                if archive_enricher is not None and candidate.archive_members is None:
                    omitted = candidate.model_dump()
                    omitted.update(
                        coverage="omitted_over_limit",
                        requested_document=None,
                        omitted_dispositions=candidate.omitted_dispositions
                        or len(candidate.dispositions),
                        dispositions=(),
                        required_exclusions=summary,
                    )
                    recovered = archive_enricher(CandidateDispositionTrace.model_validate(omitted))
                    if recovered.archive_members is not None:
                        return recovered
                return candidate

            data = original.model_dump()
            if self._rows is not None and original.coverage == "complete":
                try:
                    complete = RequiredPartExclusions(
                        retention="complete",
                        observed_rows=self._count,
                        omitted_rows=0,
                        rows_sha256=digest.hexdigest(),
                        rows=tuple(self._rows),
                    )
                    return retained(
                        CandidateDispositionTrace.model_validate(
                            {**data, "required_exclusions": complete}
                        )
                    )
                except ValueError:
                    pass
            data["required_exclusions"] = summary
            try:
                return retained(CandidateDispositionTrace.model_validate(data))
            except ValueError:
                pass
            members = original.archive_members
            if members is not None and members.observed_rows:
                data["archive_members"] = members.model_copy(
                    update={
                        "retention": "omitted",
                        "omitted_rows": members.observed_rows,
                        "rows": (),
                    }
                )
            try:
                return retained(CandidateDispositionTrace.model_validate(data))
            except ValueError:
                pass
            data.update(
                coverage="omitted_over_limit",
                requested_document=None,
                omitted_dispositions=original.omitted_dispositions or len(original.dispositions),
                dispositions=(),
            )
            return retained(CandidateDispositionTrace.model_validate(data))
        except Exception:
            return original
