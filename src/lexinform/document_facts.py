from lexinform.models.documents import DocumentFact, DocumentPart, FactKind


def span_fact(
    kind: FactKind, value: str, index: int, part: DocumentPart, start: int, end: int
) -> DocumentFact:
    return DocumentFact(
        kind=kind,
        value=value,
        provenance="extracted_text",
        part_index=index,
        source_url=part.url,
        text_sha256=part.text_sha256,
        block_id=f"part:{index}",
        start=start,
        end=end,
        quote=part.text[start:end],
    )
