"""Prospective graph admission inspects typed URL fields without creating graph identities."""

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain

from lexinform.metadata_omissions import MetadataUrlOmissionCollector
from lexinform.metadata_urls import MetadataUrlDecision, metadata_url_decision
from lexinform.models import DocumentSelection, MetadataUrlOmissions, TextDocument


@dataclass(frozen=True)
class SourceGraphAdmission:
    omissions: MetadataUrlOmissions | None = None

    @property
    def admitted(self) -> bool:
        return self.omissions is None


def _document_urls(document: TextDocument) -> Iterator[str]:
    pending = [iter((document,))]
    while pending:
        current = next(pending[-1], None)
        if current is None:
            pending.pop()
            continue
        yield from chain(
            (current.url,),
            current.extra_urls,
            current.normative_urls,
            current.required_urls,
            current.amendment_urls,
        )
        pending.append(iter(current.alternatives))


def source_graph_url_decisions(
    requested: TextDocument,
    resolved: TextDocument | None,
    selection: DocumentSelection | None,
    *,
    opaque_failure_prose: bool,
) -> Iterator[MetadataUrlDecision]:
    if opaque_failure_prose:
        yield MetadataUrlDecision(None, "opaque_failure_prose")
    urls = chain(
        _document_urls(requested),
        _document_urls(resolved) if resolved is not None else (),
        (part.url for part in chain(selection.parts, selection.observed_parts) if part.url)
        if selection is not None
        else (),
        (fact.source_url for fact in selection.facts if fact.source_url)
        if selection is not None
        else (),
    )
    for url in urls:
        yield metadata_url_decision(url)


def source_graph_admission(
    requested: TextDocument,
    resolved: TextDocument,
    selection: DocumentSelection,
    *,
    opaque_failure_prose: bool,
) -> SourceGraphAdmission:
    """Count unsafe field occurrences; empty physical URLs remain unknown rather than invented."""
    collector = MetadataUrlOmissionCollector()
    for decision in source_graph_url_decisions(
        requested, resolved, selection, opaque_failure_prose=opaque_failure_prose
    ):
        if decision.reason is not None:
            collector.observe(decision.reason, requested_url_sha256=decision.requested_url_sha256)
    return SourceGraphAdmission(collector.snapshot())
