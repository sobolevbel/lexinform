import pytest

from lexinform.models import DocumentPart, DocumentSelection, TextDocument
from lexinform.models.documents import ExtractedDocument
from tests.fakes import FakeTextExtractor
from tests.harness import World, print_url

TITLE = "Projekt ustawy o cudzoziemcach"
BODY = "USTAWA\no cudzoziemcach\n" + "Art. 1. Cudzoziemiec składa wniosek w ciągu 30 dni. " * 25
NEXT = "https://api.test/next-edition.pdf"


class PdfText(FakeTextExtractor):
    def __init__(self, *, failed_body: bytes | None) -> None:
        super().__init__(by_content={b"%PDF": BODY, b"%PDF-next": BODY})
        self.failed_body = failed_body

    def extract_document(self, data: bytes) -> ExtractedDocument:
        losses = ("PDF text extraction failed on 1 page(s)",) if data == self.failed_body else ()
        return ExtractedDocument(
            text=self.extract(data),
            selection=DocumentSelection(parts=(DocumentPart(losses=losses),)),
        )


@pytest.mark.parametrize("failed_baseline", [False, True])
def test_known_partial_analysis_cannot_certify_an_identical_visible_text(
    failed_baseline: bool,
) -> None:
    world = World(extractor=PdfText(failed_body=b"%PDF" if failed_baseline else None))
    world.add_bill("3039", TITLE)
    world.run()
    before = world.bill("3039")
    assert before.analysis is not None, "the first analysis was saved"
    assert before.analysis.selection.has_normative_pdf_page_failure == failed_baseline
    world.gateway.files[NEXT] = b"%PDF-next"

    replacement = world.analysis.reanalyze_bill(before, TextDocument(url=NEXT, kind="print"))

    assert (replacement is not None) == failed_baseline
    assert len(world.llm.contexts) == (2 if failed_baseline else 1)


def test_failed_reread_does_not_supply_the_missing_legacy_law_digest() -> None:
    original = World(extractor=PdfText(failed_body=None))
    original.add_bill("3039", TITLE)
    original.run()
    baseline = original.bill("3039").analysis
    assert baseline is not None, "the first run saved the baseline"
    restarted = World(extractor=PdfText(failed_body=b"%PDF"))
    restarted.repo.restore(original.repo.dump())
    restarted.repo.save_analysis(10, "3039", baseline.model_copy(update={"law_sha256": None}))
    restarted.gateway.files[print_url("3039")] = b"%PDF"
    restarted.gateway.files[NEXT] = b"%PDF-next"
    assert isinstance(restarted.extractor, PdfText), "the world uses the supplied reader"
    restarted.extractor.by_content[b"%PDF-next"] = BODY + "\nUZASADNIENIE\nDodatkowa informacja."

    replacement = restarted.analysis.reanalyze_bill(
        restarted.bill("3039"), TextDocument(url=NEXT, kind="print")
    )

    assert replacement is not None
    assert len(restarted.llm.contexts) == 1
    assert not restarted.llm.change_contexts
