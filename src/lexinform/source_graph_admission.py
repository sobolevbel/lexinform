"""Prospective graph admission inspects typed URL fields without creating graph identities."""

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain

from lexinform.metadata_omissions import MetadataUrlOmissionCollector
from lexinform.metadata_urls import metadata_url_decision
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


def source_graph_admission(
    requested: TextDocument,
    resolved: TextDocument,
    selection: DocumentSelection,
    *,
    opaque_failure_prose: bool,
) -> SourceGraphAdmission:
    """Count unsafe field occurrences; empty physical URLs remain unknown rather than invented."""
    collector = MetadataUrlOmissionCollector()
    if opaque_failure_prose:
        collector.observe("opaque_failure_prose", requested_url_sha256=None)
    urls = chain(
        _document_urls(requested),
        _document_urls(resolved),
        (part.url for part in chain(selection.parts, selection.observed_parts) if part.url),
        (fact.source_url for fact in selection.facts if fact.source_url),
    )
    for url in urls:
        decision = metadata_url_decision(url)
        if decision.reason is not None:
            collector.observe(decision.reason, requested_url_sha256=decision.requested_url_sha256)
    return SourceGraphAdmission(collector.snapshot())
