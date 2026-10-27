import datetime as dt
from pathlib import Path

import pytest

from lexinform.adapters.llm_prompts import build_amendments_prompt
from lexinform.models import AmendmentsContext, Attachment, PrintInfo, PublicationKind, Stage
from tests.fakes import FakeTextExtractor
from tests.harness import REFERRED, World, print_url

FIXTURES = Path(__file__).parents[1] / "fixtures/sejm"
SUBJECT = "o asystencji osobistej osób z niepełnosprawnościami"
STAGE = Stage(
    stage_type="SenatePosition",
    stage_name="Stanowisko Senatu",
    date=dt.date(2026, 10, 13),
    position="wniósł poprawki",
    print_number="3213",
)


def world_with_sources(files: dict[str, str], *, workers: int = 1) -> World:
    extractor = FakeTextExtractor(by_content={name.encode(): text for name, text in files.items()})
    world = World(extractor=extractor, text_budget_chars=60_000, workers=workers)
    world.add_bill("1929", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    world.run()
    attachments = tuple(
        Attachment(
            print_number="3213",
            name=name,
            url="https://api.test/" + name,
        )
        for name in files
    )
    world.gateway.prints["3213"] = PrintInfo(
        term=10,
        number="3213",
        title="Uchwała Senatu w sprawie ustawy " + SUBJECT,
        attachments=attachments,
    )
    for attachment in attachments:
        world.gateway.files[attachment.url] = attachment.name.encode()
    world.set_stages("1929", REFERRED + (STAGE,))
    world.clock.advance(days=1)
    return world


@pytest.mark.parametrize(
    "name",
    [
        "3889-uchwała.docx",
        "3213-uchw.docx",
        "3213.docx",
        "tekst.DOCX",
        "tekst.doc",
        "3213-001.docx",
        "uzasadnienie.docx",
        "3213.pdf",
    ],
)
def test_content_chooses_the_resolution_regardless_of_its_filename(name: str) -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    w = world_with_sources(
        {
            "3213-uchwała.docx": (FIXTURES / "senate_3213_cover.txt").read_text(),
            "3213-uzas.docx": "Uzasadnienie\nSenat proponuje poprawki do ustawy " + SUBJECT,
            name: resolution,
        }
    )

    report = w.run()

    assert not report.errors
    assert len(w.llm.amendment_contexts) == 1
    assert w.llm.amendment_contexts[0].text == resolution
    change = w.repo.list_status_changes(10, "1929")[-1]
    assert change.amendments and change.amendments.source_url == "https://api.test/" + name


@pytest.mark.parametrize(
    "failure",
    [
        "cover",
        "wrong_subject",
        "wrong_date",
        "rejection",
        "no_end",
        "conflicting_word",
    ],
)
def test_unconfirmed_sources_never_reach_the_model(failure: str) -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    texts = {
        "cover": (FIXTURES / "senate_3213_cover.txt").read_text(),
        "wrong_subject": resolution.replace(SUBJECT, "o Funduszu Medycznym"),
        "wrong_date": resolution.replace("13 października 2026", "14 października 2026"),
        "rejection": resolution[: resolution.index(", wprowadza do jej tekstu")]
        + ", odrzuca tę ustawę.\nMARSZAŁEK SENATU\nPodpis\n",
        "no_end": resolution[: resolution.index("MARSZAŁEK SENATU")],
        "conflicting_word": resolution,
    }
    files = {"3213-uchwała.docx": texts[failure]}
    if failure == "conflicting_word":
        files["3213.docx"] = resolution.replace("31 grudnia", "30 grudnia")
    w = world_with_sources(files)

    report = w.run()

    assert not report.errors and not w.llm.amendment_contexts
    assert w.repo.list_status_changes(10, "1929")[-1].amendments is None


def test_wrong_word_cannot_hide_a_complete_pdf_for_the_correct_law() -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    w = world_with_sources(
        {
            "3213-uchwała.docx": resolution.replace(SUBJECT, "o Funduszu Medycznym"),
            "3213.pdf": resolution,
        }
    )

    w.run()

    assert len(w.llm.amendment_contexts) == 1
    change = w.repo.list_status_changes(10, "1929")[-1]
    assert change.amendments and change.amendments.source_url == "https://api.test/3213.pdf"


def test_text_is_reverified_when_the_downloaded_bytes_change() -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    w = world_with_sources({"3213.docx": resolution})
    analysis = w.analysis
    info = w.gateway.prints["3213"]
    document = analysis.senate_amendments_document(info, STAGE, titles=(SUBJECT,))
    assert document is not None
    loader = w.container.text_loader()
    for index in range(40):
        url = f"https://api.test/eviction-{index}"
        w.gateway.files[url] = b"other"
        loader.read(url)
    w.gateway.files[document.url] = b"changed"
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[b"changed"] = resolution.replace("31 grudnia", "30 grudnia")

    with pytest.raises(ValueError, match="изменился"):
        analysis.summarize_amendments(w.bill("1929"), document)

    assert not w.llm.amendment_contexts


def test_print_date_can_corroborate_a_resolution_when_the_stage_date_is_wrong() -> None:
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    w = world_with_sources({"3213.docx": resolution})
    info = w.gateway.prints["3213"].model_copy(update={"document_date": dt.date(2026, 10, 13)})
    stage = STAGE.model_copy(update={"date": dt.date(2026, 7, 13)})

    document = w.analysis.senate_amendments_document(info, stage, titles=(SUBJECT,))

    assert document is not None and document.senate is not None
    assert document.senate.adopted_on == dt.date(2026, 10, 13)


def test_automatic_tracking_queues_full_pdf_without_a_synchronous_fallback() -> None:
    w = world_with_sources({"3213.pdf": (FIXTURES / "senate_3213_cover.txt").read_text()})

    w.run()

    assert not w.llm.amendment_contexts
    assert len(w.batch.submitted) == 1
    ctx = w.batch.submitted[0].ctx
    assert isinstance(ctx, AmendmentsContext) and ctx.scan is not None
    assert ctx.text == "" and ctx.source_check is not None
    assert ctx.scan.pages == ctx.scan.of_pages and not ctx.scan.truncated


def test_a_scan_is_rejected_if_its_bytes_change_after_selection() -> None:
    w = world_with_sources({"3213.pdf": (FIXTURES / "senate_3213_cover.txt").read_text()})
    info = w.gateway.prints["3213"]
    document = w.analysis.senate_amendments_document(info, STAGE, titles=(SUBJECT,))
    assert document and document.senate and document.senate.scan
    w.gateway.files[document.url] = b"changed"

    with pytest.raises(ValueError, match="изменился"):
        w.analysis.summarize_amendments(w.bill("1929"), document)

    assert not w.batch.submitted and not w.llm.amendment_contexts


@pytest.mark.parametrize("conflict", ["subject", "date", "decision"])
def test_known_conflicts_in_pdf_cannot_be_bypassed_by_visual_fallback(conflict: str) -> None:
    text = (FIXTURES / "senate_3213_resolution.txt").read_text()
    texts = {
        "subject": text.replace(SUBJECT, "o Funduszu Medycznym"),
        "date": text.replace("13 października 2026", "14 października 2026"),
        "decision": text[: text.index(", wprowadza do jej tekstu")]
        + ", odrzuca tę ustawę.\nMARSZAŁEK SENATU\nPodpis\n",
    }
    w = world_with_sources({"3213.pdf": texts[conflict]})

    w.run()

    assert not w.batch.submitted and not w.llm.amendment_contexts


REPORT_URL = print_url("3255")


def _committee_answers_the_senate(w: World, *, proposal: str = "przyjąć poprawki") -> None:
    """Druk 3255: the committee's report on the Senate's resolution, a vote per amendment."""
    w.gateway.files[REPORT_URL] = b"report-3255"
    assert isinstance(w.extractor, FakeTextExtractor), "world_with_sources builds one"
    report = (FIXTURES / "committee_3255_senate_report.txt").read_text()
    w.extractor.by_content[b"report-3255"] = report
    work = Stage(
        stage_type="CommitteeWork",
        stage_name="Praca w komisjach nad stanowiskiem Senatu",
        date=dt.date(2026, 10, 21),
        children=(
            Stage(
                stage_type="CommitteeReport",
                stage_name="Sprawozdanie komisji",
                date=dt.date(2026, 10, 21),
                print_number="3255",
                report_file=REPORT_URL,
                proposal=proposal,
            ),
        ),
    )
    w.set_stages("1929", REFERRED + (STAGE, work))
    w.clock.advance(days=1)


def test_the_report_on_the_senates_amendments_is_read_beside_the_resolution() -> None:
    """Druk 3255 (21 Oct 2026) lists «pkt N – przyjąć» and nothing else: read alone, it was
    summarised as «the document does not describe the amendments»."""
    resolution = (FIXTURES / "senate_3213_resolution.txt").read_text()
    w = world_with_sources({"3213-uchwała.docx": resolution})
    w.run()
    _committee_answers_the_senate(w)

    w.run()

    ctx = w.llm.amendment_contexts[-1]
    assert (ctx.source_kind, ctx.text) == ("senate_amendments", resolution)
    assert ctx.proposal == "przyjąć poprawki"
    assert ctx.recommendation is not None and "pkt 35 – przyjąć" in ctx.recommendation
    prompt = build_amendments_prompt(ctx)
    assert prompt.index("TEKST DOKUMENTU Z POPRAWKAMI") < prompt.index("SPRAWOZDANIE KOMISJI")
    change = w.repo.list_status_changes(10, "1929")[-1]
    assert change.amendments is not None
    assert change.amendments.source_url == "https://api.test/3213-uchwała.docx"
    text = w.publisher.texts(PublicationKind.STATUS_UPDATE)[-1]
    assert "Что меняют поправки Сената" in text
    assert text.count(REPORT_URL) == 1  # the report once, the resolution as the amendments


def test_a_report_on_amendments_the_senate_did_not_confirm_gets_no_summary() -> None:
    w = world_with_sources({"3213-uchwała.docx": (FIXTURES / "senate_3213_cover.txt").read_text()})
    w.run()
    _committee_answers_the_senate(w)

    w.run()

    assert not w.llm.amendment_contexts
    assert w.repo.list_status_changes(10, "1929")[-1].amendments is None


@pytest.mark.parametrize("name", ["senate_9_3505_docx.txt", "senate_9_3506_docx.txt"])
def test_real_july_resolutions_use_the_bounded_chronology(name: str) -> None:
    w = world_with_sources({"resolution.docx": (FIXTURES / name).read_text()})
    subject = (
        "o zmianie ustawy o publicznym transporcie zbiorowym oraz niektórych innych ustaw"
        if "3505" in name
        else "o zmianie ustawy o emeryturach pomostowych oraz niektórych innych ustaw"
    )
    info = w.gateway.prints["3213"].model_copy(
        update={
            "title": "Uchwała Senatu w sprawie ustawy " + subject,
            "document_date": dt.date(2023, 7, 19),
        }
    )
    stage = STAGE.model_copy(update={"date": dt.date(2023, 7, 13)})

    document = w.analysis.senate_amendments_document(info, stage, titles=(subject,))

    assert document and document.senate and document.senate.adopted_on == dt.date(2023, 7, 14)
