import datetime as dt
import hashlib
import logging
import re
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Literal

from lexinform.errors import OrkaUnreachableError, ServiceUnavailableError
from lexinform.models import (
    DocumentPart,
    DocumentSelection,
    PrintInfo,
    SenateResolutionIdentity,
    TextDocument,
)
from lexinform.senate_documents import (
    inspect_senate_resolution,
    resolution_date_matches,
    senate_subject,
    senate_subject_key,
)
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.documents import LoadedFile, TextLoader
from lexinform.services.senate_inspection import SenateInspectionCapture

log = logging.getLogger(__name__)


def resolve_senate_document(
    loader: TextLoader,
    info: PrintInfo,
    *,
    adopted_on: dt.date,
    titles: tuple[str, ...],
    passed_on: dt.date | None = None,
    capture: SenateInspectionCapture | None = None,
) -> TextDocument | None:
    subjects = [senate_subject(info.title)]
    for title in titles:
        match = re.search(r"\bprojekt\s+ustawy\b", title, re.I)
        subjects.append(title[match.end() :] if match else title)
    keys = {senate_subject_key(subject) for subject in subjects if subject}
    if capture is not None:
        capture.subjects(tuple(dict.fromkeys(subject for subject in subjects if subject)))
    visual: list[TextDocument] = []
    visual_indices: list[int] = []
    # Word has explicit body structure; the PDF may contain a readable cover and scanned pages.
    for extensions in ({".docx", ".docm", ".odt", ".doc"}, {".pdf"}):
        candidates: list[tuple[TextDocument, str]] = []
        candidate_indices: list[int] = []
        incomplete = False
        for index, attachment in enumerate(info.attachments):
            if attachment.print_number != info.number:
                if capture is not None:
                    capture.skip(index, "foreign_print")
                continue
            if PurePosixPath(attachment.name).suffix.casefold() not in extensions:
                if capture is not None and PurePosixPath(attachment.name).suffix.casefold() not in {
                    ".docx",
                    ".docm",
                    ".odt",
                    ".doc",
                    ".pdf",
                }:
                    capture.skip(index, "unsupported_extension")
                continue
            phase: Literal["word", "pdf"] = "pdf" if ".pdf" in extensions else "word"
            if capture is not None:
                capture.start(index, phase)
            try:
                loaded = loader.read(attachment.url)
            except ServiceUnavailableError as exc:
                if capture is not None:
                    capture.failed(exc, "source_unavailable")
                raise
            except OrkaUnreachableError as exc:
                if capture is not None:
                    capture.failed(exc, "missing" if exc.file_is_missing else "source_unavailable")
                if not exc.file_is_missing:
                    raise
                log.warning("Senate attachment unavailable: %s", attachment.url)
                incomplete = True
                continue
            except Exception as exc:
                if capture is not None:
                    capture.failed(exc, "read_error")
                log.warning("Senate attachment %s unreadable: %s", attachment.url, exc)
                incomplete = True
                continue
            if capture is not None:
                capture.read(loader, attachment.url, loaded)
            word_eof = not attachment.is_pdf and loaded.pages == 0
            evidence = inspect_senate_resolution(loaded.text or "", word_eof=word_eof)
            reason = evidence.limitation
            if not reason and evidence.outcome != "amendments":
                reason = "not_amendments"
            if not reason and senate_subject_key(evidence.subject) not in keys:
                reason = "subject_mismatch"
            if not reason and (
                evidence.adopted_on is None
                or not resolution_date_matches(
                    evidence.adopted_on,
                    stage=adopted_on,
                    document=info.document_date,
                    passed=passed_on or evidence.passed_on,
                )
            ):
                reason = "date_mismatch"
            if reason or loaded.sha256 is None:
                log.info("Senate attachment %s: %s", attachment.url, reason or "missing_hash")
                if (
                    attachment.is_pdf
                    and loaded.pages > 0
                    and loaded.sha256
                    and keys
                    and reason
                    in {
                        "no_resolution_heading",
                        "missing_resolution_end",
                        "unreadable_amendment_page",
                        "missing_amendment_body",
                    }
                    and (not evidence.subject or senate_subject_key(evidence.subject) in keys)
                ):
                    expected = tuple(dict.fromkeys(subject for subject in subjects if subject))
                    visual.append(
                        TextDocument(
                            url=attachment.url,
                            kind="senate_amendments",
                            senate=SenateResolutionIdentity(
                                sha256=loaded.sha256,
                                subject=expected[0],
                                adopted_on=adopted_on,
                                scan=True,
                                expected_subjects=expected,
                                document_date=info.document_date,
                                passed_on=passed_on,
                                pages=loaded.pages,
                            ),
                        )
                    )
                    visual_indices.append(index)
                if capture is not None:
                    capture.inspected(
                        evidence,
                        word_eof=word_eof,
                        reason=reason or "missing_hash",
                        visual=bool(visual_indices and visual_indices[-1] == index),
                    )
                continue
            assert evidence.adopted_on is not None
            candidates.append(
                (
                    TextDocument(
                        url=attachment.url,
                        kind="senate_amendments",
                        senate=SenateResolutionIdentity(
                            sha256=loaded.sha256,
                            subject=evidence.subject,
                            adopted_on=evidence.adopted_on,
                            word_eof=evidence.word_eof,
                        ),
                    ),
                    re.sub(r"\s+", " ", evidence.text).strip(),
                )
            )
            candidate_indices.append(index)
            if capture is not None:
                capture.inspected(
                    evidence, word_eof=word_eof, reason="", normalized=candidates[-1][1]
                )
        if capture is not None:
            capture.phase("pdf" if ".pdf" in extensions else "word", incomplete=incomplete)
        if candidates:
            if incomplete or len({text for _, text in candidates}) != 1:
                if capture is not None:
                    capture.result(
                        "incomplete_competing_phase"
                        if incomplete
                        else "conflicting_competing_phase"
                    )
                log.warning("Senate print %s: unresolved competing attachments", info.number)
                if ".pdf" in extensions:
                    return None
                continue
            if capture is not None:
                capture.result(
                    "matching_equivalent_pdf"
                    if ".pdf" in extensions
                    else "matching_equivalent_word",
                    candidate_indices[0],
                )
            return candidates[0][0]
    if capture is not None:
        if len(visual) == 1:
            capture.result("unique_visual_fallback", visual_indices[0])
        elif visual:
            capture.result("nonunique_visual_fallback")
    return visual[0] if len(visual) == 1 else None


def resolution_source_read(
    loader: TextLoader,
    document: TextDocument,
    *,
    sha256: str | None,
    pages: int,
    text: str | None,
) -> DocumentSourceRead | None:
    """Physical source metadata does not claim resolution admission or normative coverage."""
    if sha256 is None:
        return None
    selection = DocumentSelection(
        question="senate_resolution_source",
        policy_version="senate-source-observation-v1",
        parts=(
            DocumentPart(
                url=document.url,
                sha256=sha256,
                pages=pages,
                text_sha256=hashlib.sha256(text.encode()).hexdigest() if text is not None else "",
            ),
        ),
    )
    source = loader.capture_source_read(
        document, document, LoadedFile(text, pages=pages, sha256=sha256, selection=selection)
    )
    return source if isinstance(source, DocumentSourceRead) else None


def verified_senate_text(
    loader: TextLoader,
    document: TextDocument,
    *,
    on_read: Callable[[LoadedFile], None] | None = None,
) -> str:
    identity = document.senate
    if identity is None:
        raise ValueError("Источник поправок Сената не проверен по содержимому")
    if identity.scan:
        raise ValueError("Скан постановления требует чтения всех страниц")
    loaded = loader.read(document.url)
    if on_read is not None:
        on_read(loaded)
    evidence = inspect_senate_resolution(loaded.text or "", word_eof=identity.word_eof)
    if (
        loaded.sha256 != identity.sha256
        or not evidence.complete
        or evidence.outcome != "amendments"
        or evidence.subject != identity.subject
        or evidence.adopted_on != identity.adopted_on
    ):
        raise ValueError("Документ Сената изменился или полный текст поправок не подтверждён")
    return evidence.text
