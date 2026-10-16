import datetime as dt
import logging
import re
from pathlib import PurePosixPath

from lexinform.errors import OrkaUnreachableError, ServiceUnavailableError
from lexinform.models import PrintInfo, SenateResolutionIdentity, TextDocument
from lexinform.senate_documents import (
    inspect_senate_resolution,
    resolution_date_matches,
    senate_subject,
    senate_subject_key,
)
from lexinform.services.documents import TextLoader

log = logging.getLogger(__name__)


def resolve_senate_document(
    loader: TextLoader,
    info: PrintInfo,
    *,
    adopted_on: dt.date,
    titles: tuple[str, ...],
    passed_on: dt.date | None = None,
) -> TextDocument | None:
    subjects = [senate_subject(info.title)]
    for title in titles:
        match = re.search(r"\bprojekt\s+ustawy\b", title, re.I)
        subjects.append(title[match.end() :] if match else title)
    keys = {senate_subject_key(subject) for subject in subjects if subject}
    visual: list[TextDocument] = []
    # Word has explicit body structure; the PDF may contain a readable cover and scanned pages.
    for extensions in ({".docx", ".docm", ".odt", ".doc"}, {".pdf"}):
        candidates: list[tuple[TextDocument, str]] = []
        incomplete = False
        for attachment in info.attachments:
            if attachment.print_number != info.number:
                continue
            if PurePosixPath(attachment.name).suffix.casefold() not in extensions:
                continue
            try:
                loaded = loader.read(attachment.url)
            except ServiceUnavailableError:
                raise
            except OrkaUnreachableError as exc:
                if not exc.file_is_missing:
                    raise
                log.warning("Senate attachment unavailable: %s", attachment.url)
                incomplete = True
                continue
            except Exception as exc:
                log.warning("Senate attachment %s unreadable: %s", attachment.url, exc)
                incomplete = True
                continue
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
        if candidates:
            if incomplete or len({text for _, text in candidates}) != 1:
                log.warning("Senate print %s: unresolved competing attachments", info.number)
                if ".pdf" in extensions:
                    return None
                continue
            return candidates[0][0]
    return visual[0] if len(visual) == 1 else None


def verified_senate_text(loader: TextLoader, document: TextDocument) -> str:
    identity = document.senate
    if identity is None:
        raise ValueError("Источник поправок Сената не проверен по содержимому")
    if identity.scan:
        raise ValueError("Скан постановления требует чтения всех страниц")
    loaded = loader.read(document.url)
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
