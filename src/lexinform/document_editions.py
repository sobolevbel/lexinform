"""Address known candidate normative membership using existing immutable selection metadata."""

import datetime as dt
import hashlib
import json
import re

from lexinform.models.document_editions import DocumentEdition
from lexinform.models.document_history import DocumentSelectionRecord


def candidate_edition(
    record: DocumentSelectionRecord, *, recorded_at: dt.datetime
) -> DocumentEdition | None:
    selection = record.selection
    if selection.state not in ("selected", "unverified"):
        return None
    if sum(part.role == "bill" for part in selection.parts) != 1:
        return None
    parts = tuple(
        part
        for part in selection.parts
        if part.role in ("bill", "normative_annex", "normative_continuation")
    )
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", value)
        for part in parts
        for value in (part.sha256, part.text_sha256)
    ):
        return None
    identities = [
        {
            "role": part.role,
            "sha256": part.sha256,
            "text_sha256": part.text_sha256,
            "text_start": part.text_start,
            "text_end": part.text_end,
            "page_start": part.page_start,
            "page_end": part.page_end,
        }
        for part in parts
    ]
    canonical = json.dumps(identities, sort_keys=True, separators=(",", ":"))
    return DocumentEdition(
        membership_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        member_count=len(parts),
        recorded_at=recorded_at,
    )
