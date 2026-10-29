"""Aggregate bounded loader outcomes without retaining URLs, messages or response identities."""

import hashlib
import json
import re
from collections import Counter

from lexinform.errors import (
    DownloadError,
    DownloadFailureDetails,
    ServiceUnavailableError,
)
from lexinform.models.document_reads import (
    DOWNLOAD_FAILURE_OUTCOMES,
    MAX_DOWNLOAD_DIAGNOSTIC_BYTES,
    MAX_DOWNLOAD_FAILURE_GROUPS,
    DocumentDownloadDiagnostics,
    DocumentDownloadFailureGroup,
    DownloadDiagnosticReason,
    DownloadFailureOutcome,
)

_HOST = re.compile(r"[A-Za-z0-9.:-]{1,253}")
type _GroupKey = tuple[str, str, str, str, str, str]


def download_failure(
    error: Exception,
) -> tuple[DownloadFailureOutcome, DownloadDiagnosticReason, int | None]:
    candidate = error.download_failure if isinstance(error, DownloadError) else None
    details = candidate if isinstance(candidate, DownloadFailureDetails) else None
    status = details.status_code if details else None
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    reason: DownloadDiagnosticReason = details.reason if details else "other_error"
    if reason not in ("http_status", "transport_error", "rejected_response", "failure_budget"):
        reason = "other_error"
    if reason == "failure_budget":
        return "pre_send_rejected", reason, status
    if isinstance(error, ServiceUnavailableError):
        return "source_unavailable", reason, status
    if status in (404, 410):
        return "missing", reason, status
    if reason == "rejected_response" or status in (401, 403, 429):
        return "refused", reason, status
    return "error", reason, status


class DocumentDownloadRecorder:
    """The owning TextLoader lock protects every recorder operation."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._groups: dict[_GroupKey, DocumentDownloadFailureGroup] = {}
        self._omitted = 0

    def reset(self) -> None:
        self._counts.clear()
        self._groups.clear()
        self._omitted = 0

    def started(self) -> None:
        self._counts["invocations"] += 1

    def completed(self) -> None:
        self._counts["completed"] += 1

    def cache_hit(self) -> None:
        self._counts["cache_hits"] += 1

    def failure(
        self,
        url: str,
        host: str | None,
        outcome: DownloadFailureOutcome,
        reason: DownloadDiagnosticReason,
        *,
        status_code: int | None = None,
        limit_bytes: int | None = None,
    ) -> None:
        self._counts[outcome] += 1
        try:
            requested_sha = hashlib.sha256(url.encode()).hexdigest()
        except UnicodeEncodeError:
            self._omitted += 1
            return
        safe_host = host if host is not None and _HOST.fullmatch(host) else None
        safe_limit = limit_bytes if type(limit_bytes) is int and 0 <= limit_bytes < 2**63 else None
        group = DocumentDownloadFailureGroup(
            requested_url_sha256=requested_sha,
            host=safe_host,
            outcome=outcome,
            reason=reason,
            status_code=status_code,
            limit_bytes=safe_limit,
            count=1,
        )
        key = (requested_sha, safe_host or "", outcome, reason, str(status_code), str(safe_limit))
        previous = self._groups.get(key)
        if previous is not None:
            self._groups[key] = previous.model_copy(update={"count": previous.count + 1})
            return
        if len(self._groups) == MAX_DOWNLOAD_FAILURE_GROUPS:
            last = max(self._groups)
            if key > last:
                self._omitted += 1
                return
            self._omitted += self._groups.pop(last).count
        self._groups[key] = group

    def snapshot(self) -> DocumentDownloadDiagnostics | None:
        counts = self._counts
        if not counts["invocations"] and not counts["cache_hits"]:
            return None
        groups = [self._groups[key] for key in sorted(self._groups)]
        failures = sum(counts[outcome] for outcome in DOWNLOAD_FAILURE_OUTCOMES)
        base = DocumentDownloadDiagnostics(
            invocations=counts["invocations"],
            completed=counts["completed"],
            missing=counts["missing"],
            over_limit=counts["over_limit"],
            source_unavailable=counts["source_unavailable"],
            refused=counts["refused"],
            error=counts["error"],
            pre_send_rejected=counts["pre_send_rejected"],
            unknown_host=counts["unknown_host"],
            invalid_url=counts["invalid_url"],
            unresolved=counts["invocations"] - counts["completed"] - failures,
            cache_hits=counts["cache_hits"],
            omitted_failures=failures,
        )
        payload = base.model_dump(mode="json")
        omitted = self._omitted
        while True:
            payload["examples"] = [group.model_dump(mode="json") for group in groups]
            payload["omitted_failures"] = omitted
            if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) <= (
                MAX_DOWNLOAD_DIAGNOSTIC_BYTES
            ):
                return DocumentDownloadDiagnostics.model_validate(payload)
            omitted += groups.pop().count
