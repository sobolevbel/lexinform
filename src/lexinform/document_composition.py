"""Compose existing loose-file snapshots without downloads or question-specific admission."""

from dataclasses import dataclass, replace
from typing import Literal

from lexinform.document_selection import bill_selection, deduplicate_bundle
from lexinform.document_versions import (
    join_normative_parts,
    normative_annex_key,
    regulation_support,
)
from lexinform.models.document_reads import LoadedFile
from lexinform.models.documents import DocumentPart, DocumentSelection
from lexinform.models.sejm import TextDocument
from lexinform.sections import APPENDIX_KINDS, PAGE_BREAK, carries_the_document, document_kind


@dataclass(frozen=True)
class BundleAssembly:
    main: LoadedFile
    texts: tuple[str, ...]
    evidence: tuple[DocumentPart, ...]
    missing: tuple[str, ...]
    normative: tuple[str, ...] = ()
    annex_parts: tuple[DocumentPart, ...] = ()


def start_bundle(main: LoadedFile, *, min_chars: int) -> BundleAssembly | LoadedFile:
    if main.text is None or document_kind(main.text) in APPENDIX_KINDS:
        return main
    if main.pages and not carries_the_document(main.text, min_chars=min_chars, pages=main.pages):
        return main
    return BundleAssembly(main, (main.text,), main.selection.parts, main.selection.rejected)


def required_part(
    bundle: BundleAssembly, url: str, file: LoadedFile
) -> BundleAssembly | LoadedFile:
    if file.text is None:
        return _blocked("waiting", f"{url}: required part unavailable")
    if document_kind(file.text) in APPENDIX_KINDS - {"annex"}:
        return bundle
    return replace(
        bundle,
        texts=(*bundle.texts, file.text),
        evidence=(
            *bundle.evidence,
            *(
                p.model_copy(update={"role": "normative_continuation"})
                for p in file.selection.parts
            ),
        ),
    )


def normative_part(
    bundle: BundleAssembly, url: str, file: LoadedFile | None
) -> BundleAssembly | LoadedFile:
    if file is None:
        return _blocked("waiting", f"{url}: normative annex unavailable")
    if file.text is None:
        return _blocked("waiting", f"{url}: normative annex unreadable")
    if document_kind(file.text) in APPENDIX_KINDS - {"annex"}:
        return _blocked("ambiguous", f"{url}: normative annex role not confirmed")
    return replace(
        bundle,
        normative=(*bundle.normative, file.text),
        annex_parts=(
            *bundle.annex_parts,
            *(p.model_copy(update={"role": "normative_annex"}) for p in file.selection.parts),
        ),
    )


def finish_normative(bundle: BundleAssembly) -> BundleAssembly:
    return replace(
        bundle,
        texts=(
            join_normative_parts(
                bundle.texts[0], sorted(bundle.normative, key=normative_annex_key)
            ),
            *bundle.texts[1:],
        ),
        evidence=(
            *bundle.evidence,
            *sorted(bundle.annex_parts, key=lambda part: normative_annex_key(part.text)),
        ),
    )


def amendment_part(
    bundle: BundleAssembly, url: str, file: LoadedFile
) -> BundleAssembly | LoadedFile:
    if file.text is None:
        return _blocked("waiting", f"{url}: self-amendment unavailable")
    return replace(
        bundle,
        evidence=(
            *bundle.evidence,
            *(p.model_copy(update={"role": "self_amendment"}) for p in file.selection.parts),
        ),
    )


def supporting_part(bundle: BundleAssembly, url: str, file: LoadedFile) -> BundleAssembly:
    extra = file.text
    if extra is not None and regulation_support(extra):
        return replace(bundle, missing=(*bundle.missing, f"{url}: supporting draft regulation"))
    if extra is not None:
        return replace(
            bundle, texts=(*bundle.texts, extra), evidence=(*bundle.evidence, *file.selection.parts)
        )
    return replace(bundle, missing=(*bundle.missing, f"{url}: no extracted text"))


def supporting_failure(bundle: BundleAssembly, url: str, error_type: str) -> BundleAssembly:
    return replace(bundle, missing=(*bundle.missing, f"{url}: {error_type}"))


def finish_bundle(bundle: BundleAssembly, document: TextDocument) -> LoadedFile:
    return LoadedFile(
        PAGE_BREAK.join(bundle.texts),
        pages=bundle.main.pages,
        sha256=bundle.main.sha256,
        selection=bundle.main.selection.model_copy(
            update={
                "parts": bundle.evidence,
                "rejected": bundle.missing,
                "version": document.version_basis,
            }
        ),
    )


def finalize_selection(document: TextDocument, file: LoadedFile) -> LoadedFile:
    if file.text is None or file.selection.state in ("waiting", "unsupported", "ambiguous"):
        return file
    text, parts = deduplicate_bundle(file.text or "", file.selection.parts)
    selection = file.selection.model_copy(update={"parts": parts})
    return LoadedFile(
        text,
        pages=file.pages,
        oversize=file.oversize,
        sha256=file.sha256,
        selection=bill_selection(selection, source_basis=document.version_basis),
    )


@dataclass(frozen=True)
class CandidateFallback:
    rejected: tuple[str, ...] = ()
    unavailable: bool = False
    oversize: bool = False
    ambiguous: bool = False

    def conflict(self, url: str) -> CandidateFallback:
        return replace(self, rejected=(*self.rejected, f"{url}: official source identity conflict"))

    def failure(
        self, url: str, error_type: str, message: str, *, invalid: bool
    ) -> CandidateFallback:
        return replace(
            self,
            unavailable=self.unavailable or not invalid,
            rejected=(*self.rejected, f"{url}: {error_type}: {message}"),
        )

    def consider(self, url: str, file: LoadedFile, *, multiple: bool) -> CandidateFallback | None:
        if file.text and document_kind(file.text) in APPENDIX_KINDS:
            return replace(self, rejected=(*self.rejected, f"{url}: {document_kind(file.text)}"))
        if file.text is None and not file.is_scan and multiple:
            reason = "download size limit" if file.oversize else "no readable body"
            return replace(
                self,
                oversize=self.oversize or file.oversize,
                ambiguous=self.ambiguous or file.selection.state == "ambiguous",
                rejected=(*self.rejected, *file.selection.rejected, f"{url}: {reason}"),
            )
        return None

    def result(self) -> LoadedFile:
        return LoadedFile(
            None,
            oversize=self.oversize,
            selection=DocumentSelection(
                state="waiting"
                if self.unavailable
                else "ambiguous"
                if self.ambiguous
                else "unsupported",
                rejected=self.rejected,
            ),
        )


def _blocked(state: Literal["waiting", "ambiguous"], reason: str) -> LoadedFile:
    return LoadedFile(None, selection=DocumentSelection(state=state, rejected=(reason,)))
