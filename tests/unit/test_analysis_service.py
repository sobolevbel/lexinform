"""What the model gets to see and what is stored: text sources, triage, authors."""

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import BillStatus, Mp, Triage
from tests.fakes import FakeTextExtractor
from tests.harness import World, print_url

FOREIGNER_TEXT = (
    "Art. 1. W ustawie o cudzoziemcach wprowadza się zmiany. "
    "Art. 2. Zezwolenie na pobyt czasowy wydaje wojewoda. "
    "Art. 3. Cudzoziemiec składa wniosek osobiście. " * 3
)

# --------------------------------------------------------------------------- text sources


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


def test_oversized_pdf_is_analysed_from_metadata() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.files[print_url("3039")] = b"%" * (10_000_000 + 1)  # one byte over the limit

    report = w.run()

    assert (report.analyzed, report.published) == (1, 1)
    assert w.llm.contexts[0].text_source == "metadata_only"


# --------------------------------------------------------------------------- triage


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


# --------------------------------------------------------------------------- authors

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
