"""What the model gets to see and what is stored: text sources, triage, authors."""

import pytest

from lexinform.adapters.llm_prompts import build_user_prompt
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import AnalysisRecord, BillStatus, Mp, Triage
from lexinform.services.analysis import text_digest
from tests.fakes import FakeTextExtractor
from tests.harness import World, print_url

FOREIGNER_TEXT = (
    "Art. 1. W ustawie o cudzoziemcach wprowadza się zmiany. "
    "Art. 2. Zezwolenie na pobyt czasowy wydaje wojewoda. "
    "Art. 3. Cudzoziemiec składa wniosek osobiście. " * 3
)


def test_text_digest_ignores_layout_but_not_words() -> None:
    printed = "USTAWA\nz dnia 1 lipca 2026 r.\n\n– 1 –\nArt. 1.  Cudzoziemiec   składa wniosek.\n"
    reflowed = "USTAWA z dnia 1 lipca 2026 r. Art. 1. Cudzoziemiec składa wniosek.\n- 2 -\n"
    amended = "USTAWA z dnia 1 lipca 2026 r. Art. 1. Cudzoziemiec składa wniosek osobiście."

    assert text_digest(printed) == text_digest(reflowed)
    assert text_digest(printed) != text_digest(amended)


def test_missing_pdf_falls_back_to_metadata() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", with_pdf=False)

    w.run()

    assert w.llm.contexts[0].text_source == "metadata_only"
    assert w.publisher.new_bills[0][1] is None  # no print info on the card either


def test_unreadable_pdf_falls_back_to_metadata_without_costing_an_attempt() -> None:
    w = World(extractor=FakeTextExtractor(error=ValueError("not a PDF")))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert (report.analyzed, report.analysis_failures) == (1, 0)
    assert w.llm.contexts[0].text_source == "metadata_only"
    assert w.bill("3039").analysis_attempts == 0


def test_a_file_that_opens_as_an_appendix_is_not_analysed_in_the_bills_place() -> None:
    # The file a name pointed at turned out to be a compliance table. Describing it would
    # describe the wrong document with every appearance of describing the right one.
    w = World(extractor=FakeTextExtractor("TABELA ZGODNOŚCI\nTYTUŁ PROJEKTU\n" + "u" * 4000))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert (report.analyzed, report.analysis_failures) == (1, 0)
    assert w.llm.contexts[0].text_source == "metadata_only"


def test_a_print_opening_with_its_covering_letter_is_still_analysed() -> None:
    # The counterpart of the rule above: every print opens with the letter that hands it to the
    # Marshal, so a letter at the top is not a reason to refuse the document under it.
    letter = "Szanowny Panie Marszałku,\nna podstawie art. 118 ust. 1 Konstytucji wnoszą projekt"
    w = World(extractor=FakeTextExtractor(f"{letter}\f USTAWA\nArt. 1. " + "x" * 4000))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    assert w.llm.contexts[0].text_source == "pdf"


def test_oversized_pdf_is_analysed_from_metadata() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.files[print_url("3039")] = b"%" * (10_000_000 + 1)  # one byte over the limit

    report = w.run()

    assert (report.analyzed, report.published) == (1, 1)
    assert w.llm.contexts[0].text_source == "metadata_only"


def test_confident_triage_rejection_is_stored_as_a_non_relevant_analysis() -> None:
    w = World(
        extractor=FakeTextExtractor(FOREIGNER_TEXT),
        triage=True,
        triage_script={
            "4000": Triage(affects_foreigners=False, confidence=0.95, rationale="о бананах"),
            "4001": Triage(affects_foreigners=False, confidence=0.5, rationale="не уверен"),
        },
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")  # triage says relevant: full analysis
    w.add_bill("4000", "Rządowy projekt ustawy o jakości handlowej")  # confident no
    w.add_bill("4001", "Rządowy projekt ustawy o zmianie niektórych ustaw")  # unsure: full

    report = w.run()

    assert (report.analyzed, report.triaged_out, report.published) == (2, 1, 2)
    assert [(v.number, v.reason) for v in report.rejected] == [("4000", "triage")]
    assert sorted(c.number for c in w.llm.triage_contexts) == ["3039", "4000", "4001"]
    assert sorted(c.number for c in w.llm.contexts) == ["3039", "4001"]
    rejected = w.bill("4000")
    assert rejected.status is BillStatus.ANALYZED and rejected.analysis is not None
    assert rejected.analysis.text_source == "excerpts"
    assert rejected.analysis.model == w.llm.TRIAGE_MODEL
    assert not rejected.analysis.analysis.relevant
    assert rejected.analysis.analysis.summary == "о бананах"


def test_triage_tokens_are_counted_per_model() -> None:
    w = World(
        extractor=FakeTextExtractor(FOREIGNER_TEXT),
        triage=True,
        triage_script={
            "4000": Triage(affects_foreigners=False, confidence=0.95, rationale="о бананах")
        },
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("4000", "Rządowy projekt ustawy o jakości handlowej")

    report = w.run()

    triage_in, analysis_in = w.llm.TRIAGE_TOKENS[0], w.llm.ANALYSIS_TOKENS[0]
    assert report.llm_input_tokens == 2 * triage_in + 1 * analysis_in
    assert report.llm_usage[w.llm.TRIAGE_MODEL].input == 2 * triage_in
    assert report.llm_usage[w.llm.MODEL].input == analysis_in


def test_the_run_says_which_call_spent_what() -> None:
    """One tokens figure for a whole run could not be accounted for afterwards: the run of
    13 Sept 2026 billed 316,767 input tokens with nothing to say which call made them."""
    w = World(
        extractor=FakeTextExtractor(FOREIGNER_TEXT),
        triage=True,
        triage_script={
            "4000": Triage(affects_foreigners=False, confidence=0.95, rationale="о бананах")
        },
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("4000", "Rządowy projekt ustawy o jakości handlowej")

    report = w.run()

    assert [(c.number, c.kind) for c in report.llm_calls] == [
        ("3039", "triage"),
        ("3039", "analysis"),
        ("4000", "triage"),
    ]
    assert [c.model for c in report.llm_calls] == [
        w.llm.TRIAGE_MODEL,
        w.llm.MODEL,
        w.llm.TRIAGE_MODEL,
    ]
    assert sum(c.input_tokens for c in report.llm_calls) == report.llm_input_tokens


def test_triage_sees_keyword_windows_not_the_whole_print() -> None:
    w = World(extractor=FakeTextExtractor(FOREIGNER_TEXT), triage=True)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    ctx = w.llm.triage_contexts[0]
    assert "cudzoziem" in ctx.excerpts.lower()
    assert ctx.text_chars == len(FOREIGNER_TEXT.strip())


def test_triage_failure_is_a_per_bill_failure() -> None:
    w = World(triage=True, triage_script={"3039": RuntimeError("bad json")})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert (report.analysis_failures, report.analyzed) == (1, 0)
    assert w.bill("3039").analysis_attempts == 1


def test_short_texts_skip_the_triage() -> None:
    w = World(triage=True, triage_min_chars=10_000_000)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert report.analyzed == 1
    assert w.llm.triage_contexts == []


DEPUTIES_LETTER = (
    "Druk nr 4200\nniżej podpisani posłowie wnoszą projekt ustawy:\n"
    "- o zmianie ustawy o cudzoziemcach.\n"
    "Do reprezentowania wnioskodawców w pracach nad projektem ustawy upoważniamy posła "
    "Jana Kowalskiego.\n\n"
    " (-)  Jan Kowalski;  (-)  Anna Nowak;  (-)  Piotr Zieliński.\n\n"
    "Tłoczono z polecenia Marszałka Sejmu\n\nProjekt\nUSTAWA\n" + FOREIGNER_TEXT
)
MPS = (
    Mp(id=1, first_name="Jan", last_name="Kowalski", accusative_name="Jana Kowalskiego", club="KO"),
    Mp(id=2, first_name="Anna", last_name="Nowak", club="KO"),
    Mp(id=3, first_name="Piotr", last_name="Zieliński", club="Lewica"),
)


def test_deputies_bill_gets_its_signatories_resolved_to_clubs() -> None:
    w = World(extractor=FakeTextExtractor(DEPUTIES_LETTER))
    w.gateway.mps = MPS
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")

    w.run()

    authors = w.bill("4200").authors
    assert authors is not None
    assert authors.clubs == (("KO", 2), ("Lewica", 1))
    assert (authors.representative, authors.representative_club) == ("Jan Kowalski", "KO")
    assert w.gateway.calls.count("list_mps") == 1  # the directory is fetched once per process


def test_a_print_scanned_but_for_its_letter_is_read_as_pages() -> None:
    """Druk 604 is 37 pages of images and one page of text: the letter and the signatures under
    it. The bill itself is only in the images, so the file goes to the model — without its first
    page, which is the letter — and the signatures on that page are still resolved from the text.
    """
    cover = DEPUTIES_LETTER.split("Tłoczono z polecenia Marszałka Sejmu")[0]
    extractor = FakeTextExtractor(cover, page_count=37)
    w = World(extractor=extractor)
    w.gateway.mps = MPS
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")

    w.run()

    ctx = w.llm.contexts[0]
    assert ctx.text_source == "scan" and ctx.scan is not None
    assert ctx.scan.pages == 36 and extractor.selections == [(1, 36)]
    analysis = w.bill("4200").analysis
    assert analysis is not None and analysis.text_sha256 == ctx.scan.sha256
    authors = w.bill("4200").authors
    assert authors is not None and authors.clubs == (("KO", 2), ("Lewica", 1))


def test_dropping_the_covering_letter_is_not_a_partial_reading() -> None:
    """The letter is one page naming the bill; calling the analysis "partial" because of it told
    every reader of a scanned print that the model had seen less than the document."""
    cover = DEPUTIES_LETTER.split("Tłoczono z polecenia Marszałka Sejmu")[0]
    w = World(extractor=FakeTextExtractor(cover, page_count=37))
    w.gateway.mps = MPS
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")

    w.run()

    ctx = w.llm.contexts[0]
    assert ctx.scan is not None and not ctx.scan.truncated
    prompt = build_user_prompt(ctx)
    assert "[pominięto pismo przewodnie: 1 str.]" in prompt
    assert "OBCIĘTY" not in prompt and "OSR" not in prompt
    record = w.bill("4200").analysis
    assert record is not None and not record.truncated
    assert MessageFormatter("ru").new_bill(w.bill("4200"), None).text.count("неполном тексте") == 0


def test_a_thin_text_layer_over_many_pages_is_paper_and_not_a_document() -> None:
    """A title page or a running head left by an OCR pass reads as a document and passes any
    length threshold; against the paper it came from it does not. Measured over 30 prints of
    term 10: the thinnest real one runs 477 characters a page, the scan 139."""
    w = World(extractor=FakeTextExtractor("Druk nr 4200. Projekt ustawy. " * 20, page_count=30))
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")

    w.run()

    ctx = w.llm.contexts[0]
    assert ctx.text_source == "scan" and ctx.scan is not None and ctx.scan.pages == 30


def test_a_word_file_with_no_pages_is_read_as_the_text_it_has() -> None:
    """`without_cover_letter` cuts at a page break or a heading, and a Word file has neither to
    offer; there are no pages to fall back on either, so the text is all there will ever be."""
    letter = (
        "Szanowny Panie Marszałku, na podstawie art. 118 ust. 1 Konstytucji"
        " wnoszą projekt ustawy o zmianie ustawy o cudzoziemcach. "
    )
    body = "Zmiany dotyczą pobytu czasowego cudzoziemców. " * 200
    w = World(extractor=FakeTextExtractor(letter + body, page_count=0))
    w.add_rcl_project()

    w.run()

    ctx = w.llm.contexts[0]
    assert ctx.text_source == "documents" and ctx.scan is None


def test_card_shows_signatory_clubs_and_the_representative() -> None:
    w = World(extractor=FakeTextExtractor(DEPUTIES_LETTER))
    w.gateway.mps = MPS
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")
    w.run()

    text = MessageFormatter("ru").new_bill(w.bill("4200"), None).text

    assert (
        "Инициатор:</b> депутатский (подписали: KO 2, Lewica 1 · представитель: Jan Kowalski, KO)"
        in text
    )


def test_government_bill_does_not_fetch_the_mp_directory() -> None:
    w = World()
    w.add_bill("4201", "Rządowy projekt ustawy o zmianie ustawy o cudzoziemcach")

    w.run()

    assert "list_mps" not in w.gateway.calls
    assert w.bill("4201").authors is None


def test_text_over_the_per_bill_cost_limit_is_skipped_without_a_model_call() -> None:
    # 9.8k chars ≈ 4.9k tokens ≈ $0.025 of Opus input, over a $0.01 limit
    w = World(extractor=FakeTextExtractor("Tekst ustawy. " * 700), max_bill_cost_usd=0.01)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert (report.analyzed, report.analysis_skipped_cost) == (0, 1)
    assert w.llm.contexts == []
    bill = w.bill("3039")
    assert bill.status is BillStatus.SKIPPED_COST
    assert bill.last_error is not None and "exceeds the $0.01 limit" in bill.last_error


_HUGE = (
    "Tekst ustawy o niczym. " * 17_000  # 391,000 characters, far over any sane limit
    + "Art. 500. Przepis dotyczy cudzoziemców przebywających na terytorium RP. "
    + "Dalszy tekst. " * 3_000
)


def test_a_text_over_the_limit_is_cut_down_to_it_instead_of_being_dropped() -> None:
    """The bill has already been judged worth reading, and refusing it there left the reader with
    nothing and the operator with a `reset` to run by hand. What survives the cut is chosen by the
    keywords, so the passages the bill is relevant for are the ones that reach the model."""
    w = World(
        extractor=FakeTextExtractor(_HUGE), max_bill_cost_usd=0.30, text_budget_chars=1_000_000
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert (report.analyzed, report.analysis_skipped_cost) == (1, 0)
    (ctx,) = w.llm.contexts
    assert ctx.truncated and len(ctx.text) < len(_HUGE)
    assert len(ctx.text) / 2 / 1e6 * 5 <= 0.30  # what the shortened text costs, at Opus's price
    assert "cudzoziemców przebywających" in ctx.text  # the keyword window survived the cut
    assert w.bill("3039").status is BillStatus.ANALYZED


def test_a_text_that_cannot_be_cut_small_enough_is_still_refused() -> None:
    """Under `_FIT_MIN_CHARS` what is left is not a document any more, and saying so is honest."""
    w = World(
        extractor=FakeTextExtractor(_HUGE), max_bill_cost_usd=0.01, text_budget_chars=1_000_000
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert (report.analyzed, report.analysis_skipped_cost) == (0, 1)
    assert w.llm.contexts == []
    assert w.bill("3039").status is BillStatus.SKIPPED_COST


def test_a_scan_is_priced_by_its_pages_without_being_uploaded_to_be_counted() -> None:
    """A scan has no text to measure, and asking the tokenizer means uploading the file itself —
    tens of megabytes to learn a number that is 1,600 tokens a page, measured."""
    w = World(extractor=FakeTextExtractor("", page_count=4), max_bill_cost_usd=0.01)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")  # 4 pages ≈ 6400 tokens ≈ $0.032

    report = w.run()

    assert (report.analyzed, report.analysis_skipped_cost) == (0, 1)
    assert w.llm.contexts == [] and w.llm.counted == []  # the file was never sent to be counted
    bill = w.bill("3039")
    assert bill.status is BillStatus.SKIPPED_COST
    assert bill.last_error is not None and "4 scanned page(s)" in bill.last_error


def test_a_failed_count_falls_back_to_the_estimate() -> None:
    w = World(extractor=FakeTextExtractor("Tekst ustawy. " * 700), max_bill_cost_usd=0.01)
    w.llm.count_fails = True
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    report = w.run()

    assert (report.analyzed, report.analysis_skipped_cost) == (0, 1)
    reason = w.bill("3039").last_error
    assert reason is not None and "chars" in reason


def test_run_cost_limit_stops_the_phase_and_leaves_the_rest_pending() -> None:
    w = World(max_run_cost_usd=0.001)
    w.llm.MODEL = "claude-opus-5"  # priced: 100 in + 50 out per analysis ≈ $0.002
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.add_bill("3040", "Projekt ustawy o obywatelstwie polskim")

    report = w.run()

    assert (report.analyzed, report.published) == (1, 1) and not report.errors
    assert report.notes == [
        "analysis: run cost limit reached (≈$0.002 ≥ $0.001); "
        "the remaining candidates wait for the next run"
    ]
    assert w.bill("3040").status is BillStatus.ANALYSIS_PENDING


def test_a_scanned_print_is_triaged_before_the_expensive_model_reads_it() -> None:
    """The cheap pass was gated on how long the text is, and a scan's text is the letter that
    hands it to the Marshal — so the most expensive documents the project reads were the one
    thing that skipped it. Over term 10 that is 317 documents and 7,763 pages, $62 on Opus
    against the ~$44 the rest of the term costs.
    """
    cover = DEPUTIES_LETTER.split("Tłoczono z polecenia Marszałka Sejmu")[0]
    w = World(
        extractor=FakeTextExtractor(cover, page_count=37),
        triage=True,
        # Production's threshold, because that is what used to shut the scan out: the letter is
        # 800 characters and the gate wanted 20,000 of them.
        triage_min_chars=20_000,
        triage_script={
            "4200": Triage(affects_foreigners=False, confidence=0.95, rationale="o zwierzętach")
        },
    )
    w.add_bill("4200", "Poselski projekt ustawy o ochronie zwierząt")

    report = w.run()

    assert [c.number for c in w.llm.triage_contexts] == ["4200"]
    assert w.llm.contexts == []  # the expensive model never saw the pages
    assert (report.analyzed, report.triaged_out) == (0, 1)
    analysis = w.bill("4200").analysis
    assert analysis is not None and analysis.model == w.llm.TRIAGE_MODEL
    assert not analysis.analysis.relevant


def test_the_triage_is_shown_the_opening_pages_of_a_scan_and_not_all_of_them() -> None:
    """What makes the pass cheap on the documents that are dear: the cost of the cheap call stops
    depending on how thick the paper is. Druk 348 is 362 pages and costs $2.90 read whole; eight
    pages cost $0.013 whatever the document."""
    cover = DEPUTIES_LETTER.split("Tłoczono z polecenia Marszałka Sejmu")[0]
    extractor = FakeTextExtractor(cover, page_count=37)
    w = World(extractor=extractor, triage=True, triage_min_chars=20_000)
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")

    w.run()

    triaged = w.llm.triage_contexts[0]
    assert triaged.scan is not None
    assert (triaged.scan.pages, triaged.scan.of_pages) == (8, 37)
    assert triaged.excerpts == ""  # there is no text to excerpt; the pages are the evidence
    analysed = w.llm.contexts[0]
    assert analysed.scan is not None and analysed.scan.pages == 36  # the full reading is intact


def test_a_scan_short_enough_to_read_whole_is_triaged_whole() -> None:
    """Nothing to cut: below the window the scan goes to the cheap model as it stands."""
    cover = DEPUTIES_LETTER.split("Tłoczono z polecenia Marszałka Sejmu")[0]
    w = World(
        extractor=FakeTextExtractor(cover, page_count=4), triage=True, triage_min_chars=20_000
    )
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")

    w.run()

    triaged = w.llm.triage_contexts[0]
    assert triaged.scan is not None and (triaged.scan.pages, triaged.scan.of_pages) == (3, 4)


@pytest.mark.parametrize("reject", [False, True])
def test_scan_and_triage_memos_survive_a_failed_write(
    reject: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cover = DEPUTIES_LETTER.split("Tłoczono z polecenia Marszałka Sejmu")[0]
    triage = Triage(
        affects_foreigners=not reject,
        confidence=0.95,
        rationale="nie dotyczy" if reject else "dotyczy",
    )
    w = World(
        extractor=FakeTextExtractor(cover, page_count=37),
        triage=True,
        triage_min_chars=20_000,
        triage_script={"4200": triage},
    )
    w.add_bill("4200", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")
    original = w.repo.save_analysis
    failed = False

    def fail_once(term: int, number: str, record: AnalysisRecord) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("checkpoint interrupted")
        original(term, number, record)

    with monkeypatch.context() as patch:
        patch.setattr(w.repo, "save_analysis", fail_once)
        first = w.run()

    assert first.analysis_failures == 1
    assert len(w.llm.triage_contexts) == 1
    assert len(w.llm.contexts) == int(not reject)
    w.repo.restore(w.repo.dump())

    second = w.run()

    assert second.analysis_failures == 0
    assert len(w.llm.triage_contexts) == 1
    assert len(w.llm.contexts) == int(not reject)
    assert second.triaged_out == int(reject)
    assert second.analyzed == int(not reject)


def test_a_short_readable_text_still_skips_the_triage() -> None:
    """The gate on `triage_min_chars` is unchanged for a text: a short one is cheap to analyse
    whole and the cheap pass would only add a call."""
    w = World(
        extractor=FakeTextExtractor("Art. 1. Cudzoziemiec składa wniosek osobiście. " * 14),
        triage=True,
        triage_min_chars=10_000,
    )
    w.add_bill("4300", "Poselski projekt ustawy o zmianie ustawy o cudzoziemcach")

    w.run()

    assert w.llm.triage_contexts == []
    assert [c.number for c in w.llm.contexts] == ["4300"]


def test_the_default_fixture_text_is_read_as_a_document_and_not_as_a_scan() -> None:
    """Every scenario test that does not say otherwise gets `FakeTextExtractor`'s default text and
    the page count it implies, and the analysis judges a scan from a document by characters per
    page. If the two ever drift apart, three hundred tests quietly change what they are about —
    so the one that fixes the default says which side of the rule it is on."""
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    ctx = w.llm.contexts[0]
    assert ctx.text_source == "pdf" and ctx.scan is None
