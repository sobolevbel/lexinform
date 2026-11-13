import datetime as dt

from lexinform.models import BillStatus, PublicationKind, TextDocument, next_phase
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, World, act, rcl_document, rcl_folder, rcl_project, rcl_stage
from tests.unit.test_physical_extraction import archive, docx, extractor

TITLE = "Projekt rozporządzenia Ministra w sprawie opłat za zezwolenia na pracę cudzoziemców"
TEXT = "ROZPORZĄDZENIE\nMINISTRA\n§ 1. " + "Opłata wynosi 400 zł. " * 30
ELI = "DU/2026/1622"


def test_regulation_keeps_one_thread_from_consultation_to_entry_into_force() -> None:
    world = World(extractor=FakeTextExtractor(TEXT))
    draft = rcl_document(8231, "Załącznik nr 1 - Projekt rozporządzenia.pdf")
    project = world.add_rcl_project(rcl_project(title=TITLE))
    project = project.with_stage(
        rcl_stage(4, "Opiniowanie", "active", rcl_folder(84, "Projekt", draft))
    )
    world.add_rcl_project(project)
    first = world.run()

    assert first.published == 1
    assert "Opłata wynosi 400 zł" in world.llm.contexts[0].text
    card = world.publisher.texts(PublicationKind.NEW_BILL)[0]
    assert "Проект подзаконного акта" in card and "Сейм" not in card
    assert "dep.prawny@mswia.gov.pl" in card

    world.clock.advance(days=2)
    publication = project.model_copy(
        update={
            "status": "zamknięty",
            "modified": world.clock.now().date(),
            "published_eli": ELI,
            "stages": (
                *project.stages,
                rcl_stage(15, "Skierowanie aktu do ogłoszenia", "reached"),
            ),
        }
    )
    world.rcl.put(publication)
    missing = world.run()
    pending = world.bill(RCL)
    phase = next_phase(pending, today=world.clock.now().date())
    assert missing.acts_published == 0 and phase is not None
    assert phase.key == "in_force_unknown"
    assert pending.act is None
    assert not world.publisher.texts(PublicationKind.IN_FORCE)

    world.clock.advance(days=3)
    finalized = act(
        eli=ELI,
        title="Rozporządzenie " + TITLE.removeprefix("Projekt rozporządzenia "),
        promulgation_date=publication.modified,
        fetched_at=world.clock.now(),
        entry_into_force=world.clock.now().date() + dt.timedelta(days=1),
    )
    world.gateway.acts[ELI] = finalized
    result = world.run()

    assert result.acts_published == 1
    assert world.publisher.acts[0][1] == world.card_id(RCL)
    notice = world.publisher.texts(PublicationKind.ACT_PUBLISHED)[0]
    assert f"https://eli.gov.pl/eli/{ELI}/ogl" in notice
    assert "#rozporządzenie" in notice and "Проект меняет правила" not in notice
    assert world.bill(RCL).act == finalized
    assert world.run().acts_published == 0

    world.clock.advance(days=1)
    assert world.run().in_force_posted == 1
    assert world.publisher.in_force[0][1] == world.card_id(RCL)
    assert world.run().in_force_posted == 0


def test_regulation_first_seen_after_entry_into_force_is_history() -> None:
    world = World()
    world.add_rcl_project(rcl_project(title=TITLE, published_eli=ELI, status="zamknięty"))
    world.gateway.acts[ELI] = act(
        eli=ELI,
        title="Rozporządzenie Ministra w sprawie opłat za zezwolenia na pracę cudzoziemców",
        promulgation_date=world.clock.now().date(),
        entry_into_force=world.clock.now().date(),
    )

    report = world.run()

    assert report.over_on_arrival == 1 and report.analyzed == 0 and report.published == 0
    assert world.bill(RCL).status is BillStatus.SKIPPED_CLOSED


def test_regulation_first_seen_before_entry_into_force_opens_with_official_date() -> None:
    world = World(extractor=FakeTextExtractor(TEXT))
    today = world.clock.now().date()
    effective = today + dt.timedelta(days=10)
    published = act(
        eli=ELI,
        title=TITLE.removeprefix("Projekt ").replace("rozporządzenia", "Rozporządzenie", 1),
        promulgation_date=today,
        entry_into_force=effective,
    )
    assert published.text_pdf_url is not None, "the fake ELI act defaults to an official PDF"
    world.gateway.acts[ELI] = published
    world.gateway.files[published.text_pdf_url] = b"%PDF-published-regulation"
    world.add_rcl_project(rcl_project(title=TITLE, published_eli=ELI, status="zamknięty"))

    report = world.run()

    assert report.published == 1 and report.acts_published == 0
    card = world.publisher.texts(PublicationKind.NEW_BILL)[0]
    assert "Опубликованное rozporządzenie" in card and world.formatter.fmt_date(effective) in card
    assert "через 14 дней" not in card and "работа над ним ещё не началась" not in card
    assert "Текст rozporządzenia (PDF)" in card
    world.clock.advance(days=10)
    assert world.run().in_force_posted == 1
    assert not world.publisher.texts(PublicationKind.ACT_PUBLISHED)


def test_regulation_revision_changes_the_analyzed_amount() -> None:
    revised_bytes = b"%PDF-revised-regulation"
    reader = FakeTextExtractor(TEXT, by_content={revised_bytes: TEXT.replace("400", "800")})
    world = World(extractor=reader)
    original = rcl_document(8231, "Projekt rozporządzenia.pdf")
    stage = rcl_stage(4, "Opiniowanie", "active", rcl_folder(84, "Projekt", original))
    project = world.add_rcl_project(rcl_project(title=TITLE, stages=(stage,)))
    world.run()
    world.clock.advance(days=2)
    revision = rcl_document(8232, "Projekt po uzgodnieniach.pdf", created=world.clock.now().date())
    modified = world.clock.now().date()
    revised = project.model_copy(
        update={
            "modified": modified,
            "stages": (
                stage,
                rcl_stage(
                    10,
                    "Komisja Prawnicza",
                    "active",
                    rcl_folder(85, "Projekt", revision),
                    modified=modified,
                ),
            ),
        }
    )
    world.add_rcl_project(revised)
    world.rcl.files[revision.url] = revised_bytes

    report = world.run()

    assert report.reanalyzed == 1
    assert world.llm.contexts[-1].previous_summary is not None
    assert "Opłata wynosi 800 zł" in world.llm.contexts[-1].text
    assert world.run().reanalyzed == 0


def test_regulation_archive_keeps_its_normative_annex() -> None:
    world = World(extractor=extractor())
    draft = rcl_document(8231, "Projekt rozporządzenia.zip")
    world.rcl.files[draft.url] = archive(
        {
            "projekt.docx": docx(TEXT),
            "zalacznik.docx": docx(
                "Załącznik do rozporządzenia\n" + "Wykaz opłat dodatkowych. " * 10
            ),
        }
    )
    sources = world.container.text_loader()
    document = TextDocument(url=draft.url, kind="rcl", regulation=True, compare_versions=True)

    _, selected = sources.select_document(document)

    assert selected.text and "Wykaz opłat dodatkowych" in selected.text
    assert [part.role for part in selected.selection.parts] == ["bill", "normative_annex"]


def test_regulation_bundle_accepts_support_identifying_the_regulation_as_its_subject() -> None:
    justification = b"%PDF-justification"
    osr = b"%PDF-osr"
    reader = FakeTextExtractor(
        TEXT,
        by_content={
            justification: f"UZASADNIENIE\n{TITLE}\n" + "Uzasadnienie zmiany. " * 25,
            osr: f"Nazwa projektu\n{TITLE}\n" + "Ocena skutków regulacji. " * 25,
        },
    )
    world = World(extractor=reader)
    draft, reasons, impact = (
        rcl_document(8231, "Projekt rozporządzenia.pdf"),
        rcl_document(8232, "Uzasadnienie.pdf"),
        rcl_document(8233, "OSR.pdf"),
    )
    stage = rcl_stage(4, "Opiniowanie", "active", rcl_folder(84, "Projekt", draft, reasons, impact))
    world.add_rcl_project(rcl_project(title=TITLE, stages=(stage,)))
    world.rcl.files[reasons.url] = justification
    world.rcl.files[impact.url] = osr

    report = world.run()

    assert report.analyzed == 1
    assert "Uzasadnienie zmiany" in world.llm.contexts[0].text
    assert "Ocena skutków regulacji" in world.llm.contexts[0].text
