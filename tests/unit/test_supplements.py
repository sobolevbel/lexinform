"""Documents filed to a print after its submission: what is told, what is not, and once."""

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import new_supplements, supplement_kind
from tests.fakes import FakeTextExtractor, make_digest
from tests.harness import COMMITTEE_STAGES, World

GOVERNMENT_POSITION = "Stanowisko Rządu do druku nr 3039."
OSR = "Do druku nr 3039 - ocena skutków regulacji"
OPINION = "Do druku nr 3039 - opinia SN"
NO_REMARKS = "Do druku nr 3039 - opinia PG RP ( nie zgłoszono uwag )."
HOUSEKEEPING = "Do druku nr 3039 - zmiana posła sprawozdawcy"
TITLE = "Projekt ustawy o cudzoziemcach"
PM_LETTER = """Warszawa, 21 sierpnia 2025 r.
Prezes Rady Ministrów
DSP.WPP.0640.57.2025
 Pan
 Marszałek Sejmu
Szanowny Panie Marszałku,
przekazuję przyjęte przez Radę Ministrów stanowisko w sprawie poselskiego projektu ustawy
- o zmianie ustawy o obywatelstwie polskim (druk nr 1273).
Jednocześnie informuję, że Rada Ministrów upoważniła Ministra Spraw Wewnętrznych
i Administracji do prezentowania stanowiska Rządu w tej sprawie w toku prac parlamentarnych.
Z wyrazami szacunku, Donald Tusk
"""


def test_the_kinds_worth_telling_are_told_apart_by_their_title() -> None:
    assert supplement_kind(GOVERNMENT_POSITION) == "government_position"
    assert supplement_kind(OSR) == "impact_assessment"
    assert supplement_kind(OPINION) == "opinion"
    assert supplement_kind(NO_REMARKS) is None
    assert supplement_kind(HOUSEKEEPING) is None


def test_new_supplements_are_those_the_print_has_and_the_bill_has_not_seen() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.file_to_print("3039", GOVERNMENT_POSITION)
    w.file_to_print("3039", OSR, suffix="001")
    print_info = w.gateway.prints["3039"]

    assert [p.number for p in new_supplements(print_info, ())] == ["3039-s", "3039-001"]
    assert [p.number for p in new_supplements(print_info, ["3039-s"])] == ["3039-001"]
    assert new_supplements(None, ()) == ()


def test_the_documents_a_print_already_has_at_first_sight_are_seeded_silently() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.file_to_print("3039", GOVERNMENT_POSITION)

    w.run()

    assert w.publisher.updates == []
    assert w.bill("3039").seen_supplements == ("3039-s",)
    assert w.llm.supplement_contexts == []


def test_the_governments_position_is_told_with_what_it_says() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    url = w.file_to_print("3039", GOVERNMENT_POSITION)
    w.clock.advance(days=1)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert (report.updates, again.updates) == (1, 0)
    _, change, reply_to = w.publisher.updates[0]
    assert reply_to == w.card_id("3039")
    assert [r.number for r in change.supplements] == ["3039-s"]
    record = change.supplements[0]
    assert record.source_kind == "government_position" and record.source_url == url
    assert record.digest is not None and record.digest.supports is True
    assert len(w.llm.supplement_contexts) == 1  # not digested again
    assert w.llm.supplement_contexts[0].previous_summary
    text = MessageFormatter("ru").status_update(*w.publisher.updates[0][:2]).text
    assert text.startswith("🏛 <b>Правительство высказалось о проекте — druk nr 3039</b>")
    assert "📄 <b>Позиция правительства по проекту</b>" in text
    assert "<b>правительство поддерживает проект</b>" in text
    assert "Правительство просит смягчить требования к сроку пребывания." in text
    assert f'href="{url}"' in text
    assert "#правительство" in text


def test_a_document_that_raised_nothing_is_recorded_and_not_told() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.file_to_print("3039", NO_REMARKS, suffix="002")
    w.file_to_print("3039", HOUSEKEEPING, suffix="003")
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 0 and w.publisher.updates == []
    assert w.bill("3039").seen_supplements == ("3039-002", "3039-003")
    assert w.llm.supplement_contexts == []


def test_several_documents_filed_at_once_are_told_in_one_reply() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.file_to_print("3039", OSR, suffix="001")
    w.file_to_print("3039", OPINION, suffix="002")
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1
    _, change, _ = w.publisher.updates[0]
    assert [r.source_kind for r in change.supplements] == ["impact_assessment", "opinion"]
    text = MessageFormatter("ru").status_update(*w.publisher.updates[0][:2]).text
    assert text.startswith("📊 <b>Появилась оценка последствий проекта — druk nr 3039</b>")
    assert "📄 <b>Оценка последствий (OSR)</b>" in text
    assert "📄 <b>Мнение по проекту</b>" in text


def test_a_document_arriving_with_a_stage_is_told_under_the_stages_header() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.file_to_print("3039", OSR, suffix="001")
    w.set_stages("3039", COMMITTEE_STAGES)
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1
    _, change, _ = w.publisher.updates[0]
    assert len(change.new_stages) == 2 and len(change.supplements) == 1
    text = MessageFormatter("ru").status_update(*w.publisher.updates[0][:2]).text
    assert "📄 <b>Оценка последствий (OSR)</b>" in text
    assert not text.startswith("📊")  # the stages name the post, the document is its body


def test_an_unreadable_document_is_still_named_and_linked() -> None:
    w = World(extractor=FakeTextExtractor(error=RuntimeError("no text layer")))
    w.add_bill("3039", TITLE)
    w.run()
    url = w.file_to_print("3039", GOVERNMENT_POSITION)
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1
    _, change, _ = w.publisher.updates[0]
    assert change.supplements[0].digest is None
    assert w.llm.supplement_contexts == []
    text = MessageFormatter("ru").status_update(*w.publisher.updates[0][:2]).text
    assert "📄 <b>Позиция правительства по проекту</b>" in text
    assert GOVERNMENT_POSITION in text and f'href="{url}"' in text


def test_a_document_over_the_cost_limit_is_told_without_a_digest() -> None:
    """An OSR arrives as a 2.7 MB PDF (druk 1273); the bill's own text passes the same limit."""
    w = World(
        extractor=FakeTextExtractor(by_content={b"%PDF-filed": "Ocena skutków. " * 20_000}),
        max_bill_cost_usd=0.01,
    )
    w.add_bill("3039", TITLE)
    w.run()
    url = w.file_to_print("3039", OSR, suffix="001")
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1
    _, change, _ = w.publisher.updates[0]
    assert change.supplements[0].digest is None
    assert w.llm.supplement_contexts == []
    text = MessageFormatter("ru").status_update(*w.publisher.updates[0][:2]).text
    assert "📄 <b>Оценка последствий (OSR)</b>" in text and f'href="{url}"' in text


def test_a_filed_document_that_is_only_its_covering_letter_is_not_digested() -> None:
    """Of 66 documents filed to prints, 11 carry the Prime Minister's letter and nothing else
    (term 10, 12 Sept 2026); it names the bill and says who will present the position, never
    what the position is."""
    w = World(extractor=FakeTextExtractor(by_content={b"%PDF-filed": PM_LETTER}))
    w.add_bill("1273", TITLE)
    w.run()
    url = w.file_to_print("1273", GOVERNMENT_POSITION.replace("3039", "1273"))
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1
    _, change, _ = w.publisher.updates[0]
    assert change.supplements[0].digest is None
    assert w.llm.supplement_contexts == []
    text = MessageFormatter("ru").status_update(*w.publisher.updates[0][:2]).text
    assert "📄 <b>Позиция правительства по проекту</b>" in text and f'href="{url}"' in text


def test_a_model_failure_does_not_lose_the_document() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.llm.supplement_script[GOVERNMENT_POSITION] = RuntimeError("refusal")
    w.file_to_print("3039", GOVERNMENT_POSITION)
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1 and not report.errors
    _, change, _ = w.publisher.updates[0]
    assert change.supplements[0].digest is None
    assert w.bill("3039").seen_supplements == ("3039-s",)


def test_a_failed_reply_keeps_its_documents_when_it_is_retried() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.file_to_print("3039", GOVERNMENT_POSITION)
    w.publisher.fail_on = {"3039"}
    w.clock.advance(days=1)
    failed = w.run()
    w.publisher.fail_on = set()
    w.clock.advance(days=1)

    retried = w.run()

    assert failed.updates == 0 and retried.updates == 1
    _, change, _ = w.publisher.updates[-1]
    assert [r.number for r in change.supplements] == ["3039-s"]
    assert change.supplements[0].digest == make_digest()
    assert len(w.llm.supplement_contexts) == 1  # the retry re-reads the row, not the model


def test_a_print_that_could_not_be_read_forgets_nothing() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.file_to_print("3039", GOVERNMENT_POSITION)
    w.run()
    del w.gateway.prints["3039"]
    w.clock.advance(days=1)

    w.run()

    assert w.bill("3039").seen_supplements == ("3039-s",)


def test_a_second_document_of_the_same_kind_is_a_second_post() -> None:
    w = World()
    w.add_bill("3039", TITLE)
    w.run()
    w.file_to_print("3039", OPINION, suffix="002")
    w.clock.advance(days=1)
    w.run()
    w.file_to_print("3039", OPINION.replace("SN", "KRS"), suffix="003")
    w.clock.advance(days=1)

    report = w.run()

    assert report.updates == 1
    assert [r.number for r in w.publisher.updates[-1][1].supplements] == ["3039-003"]
