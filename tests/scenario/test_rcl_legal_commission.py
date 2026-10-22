import datetime as dt
from typing import Literal

import pytest

from lexinform.models import PublicationKind, PublicationStatus, RclProject, iso_week
from tests.fakes import FakeTextExtractor
from tests.harness import (
    CHANNEL,
    LETTER_TEXT,
    RCL,
    World,
    rcl_document,
    rcl_folder,
    rcl_project,
    rcl_stage,
)
from tests.scenario.test_document_selection_contract import law


def start_legal_review(w: World) -> RclProject:
    project = w.add_rcl_project(rcl_project())
    w.run()
    w.clock.advance(days=4)
    moved = project.model_copy(
        update={
            "modified": w.clock.now().date(),
            "stages": (
                *project.stages[:5],
                rcl_stage(10, "Komisja Prawnicza", "active", modified=w.clock.now().date()),
                *project.stages[5:],
            ),
        }
    )
    w.rcl.put(moved)
    return moved


@pytest.mark.parametrize("mode", ["on", "shadow", "off"])
def test_legal_review_without_consequences_uses_digest_only_when_policy_is_on(
    mode: Literal["on", "shadow", "off"],
) -> None:
    w = World(reader_attention=mode, attention_roundup="daily")
    start_legal_review(w)
    requests = len(w.llm.contexts)

    report = w.run()

    pub = w.publication(RCL, PublicationKind.STATUS_UPDATE)
    assert report.ok and pub is not None and pub.delivery is not None
    assert len(w.llm.contexts) == requests
    assert pub.delivery.destination == ("digest_only" if mode == "on" else "direct")
    if mode != "off":
        assert pub.delivery.proposed_destination == "digest_only"
        assert pub.delivery.destination_reason == "legal_commission_without_confirmed_consequence"
    if mode == "on":
        assert pub.status is PublicationStatus.QUEUED and pub.attempts == 0
        assert not w.publisher.texts(PublicationKind.STATUS_UPDATE)


def test_legal_review_is_delivered_only_by_its_confirmed_digest_part_after_restore() -> None:
    w = World(reader_attention="on", attention_roundup="daily")
    start_legal_review(w)
    w.run()
    source = w.publication(RCL, PublicationKind.STATUS_UPDATE)
    assert source is not None and source.id is not None
    w.repo.restore(w.repo.dump())
    w.clock.advance(hours=25)
    w.container.delivery_service(dry_run=False).run()
    assert w.repo.publication_by_id(source.id) == source
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    ref = iso_week(w.clock.now().date())

    week = service.build(ref)
    entry = next(entry for entry in week.updates if source.id in entry.source_publication_ids)
    assert entry.event == "rcl_legal_commission"
    service.draft(ref)
    assert w.repo.digest_owner(source.id) is None
    assert w.repo.publication_by_id(source.id) == source
    result = service.publish(ref)

    saved = w.repo.publication_by_id(source.id)
    owner_id = w.repo.digest_owner(source.id)
    assert result.published and result.covered_events == 1 and owner_id is not None
    owner = w.repo.publication_by_id(owner_id)
    assert saved is not None and saved.status is PublicationStatus.SENT
    assert owner is not None and owner.channel_id == CHANNEL
    assert saved.message_id == owner.message_id
    assert not w.publisher.texts(PublicationKind.STATUS_UPDATE)
    # Covered and not shown: a routine step is the card's, and the digest is a line a bill.
    assert "Юридическая проверка" not in "\n".join(w.publisher.texts(PublicationKind.DIGEST))


def test_open_consultation_is_not_hidden_by_legal_review() -> None:
    w = World(reader_attention="on")
    project = start_legal_review(w)
    letter = LETTER_TEXT.replace("7 dni", "14 dni")
    w.add_rcl_project(project, letter=letter)
    stored = w.bill(RCL)
    assert stored.rcl is not None and stored.rcl.consultation is not None
    known = stored.rcl.consultation.model_copy(
        update={"deadline": w.clock.now().date() + dt.timedelta(days=2)}
    )
    w.repo.save_rcl(
        stored.term, stored.number, stored.rcl.model_copy(update={"consultation": known})
    )

    w.run()

    pub = w.publication(RCL, PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None
    assert pub.delivery.destination == "direct" and pub.status is PublicationStatus.SENT
    assert pub.attention is not None and pub.attention.urgency == "urgent"


@pytest.mark.parametrize("workers", [1, 4])
def test_unfiled_new_text_is_reanalysed_and_keeps_its_own_post(workers: int) -> None:
    w = World(reader_attention="on", attention_roundup="daily", workers=workers)
    project = start_legal_review(w)
    document = rcl_document(
        802926,
        "KP - MC - projekt ustawy, uzasadnienie w tym OSR.docx",
        created=w.clock.now().date(),
    )
    stages = tuple(
        stage.model_copy(update={"loose_documents": (document,)})
        if stage.is_legal_commission
        else stage
        for stage in project.stages
    )
    w.rcl.put(project.model_copy(update={"stages": stages}))
    w.rcl.files[document.url] = b"%PDF-new-law"
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[b"%PDF-new-law"] = (
        "Art. 1. Cudzoziemiec składa wniosek o zezwolenie wyłącznie elektronicznie. " * 50
    )

    report = w.run()

    assert report.ok and report.reanalyzed == 1 and report.updates == 1
    bill = w.bill(RCL)
    assert bill.analysis is not None and bill.analysis.source_url == document.url
    pub = w.publication(RCL, PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None
    assert pub.delivery.destination == "direct" and pub.status is PublicationStatus.SENT
    assert "Новая версия текста" in w.publisher.texts(PublicationKind.STATUS_UPDATE)[-1]


def test_legacy_catalog_is_checked_once_even_when_its_date_has_not_changed() -> None:
    w = World(reader_attention="on")
    start_legal_review(w)
    w.run()
    bill = w.bill(RCL)
    assert bill.rcl is not None
    stages = tuple(stage.model_copy(update={"loose_documents": None}) for stage in bill.rcl.stages)
    w.repo.save_rcl(bill.term, bill.number, bill.rcl.model_copy(update={"stages": stages}))
    w.repo.restore(w.repo.dump())
    calls = sum(call.startswith("get_stage:") for call in w.rcl.calls)

    w.run(full_track=True)
    refreshed = sum(call.startswith("get_stage:") for call in w.rcl.calls)
    w.run(full_track=True)

    assert refreshed > calls
    assert sum(call.startswith("get_stage:") for call in w.rcl.calls) == refreshed
    assert not w.publisher.texts(PublicationKind.STATUS_UPDATE)


@pytest.mark.parametrize("filed_day, loose_day", [(1, 2), (2, 1)])
def test_unfiled_draft_cannot_displace_a_newer_internal_version(
    filed_day: int, loose_day: int
) -> None:
    filed = rcl_document(1, "Projekt ustawy.docx", created=dt.date(2026, 9, 4))
    loose = rcl_document(2, "KP - projekt ustawy.docx", created=dt.date(2026, 9, 5))
    legal = rcl_stage(10, "Komisja Prawnicza", "active").model_copy(
        update={"loose_documents": (loose,)}
    )
    project = rcl_project(
        stages=(
            rcl_stage(
                9, "Stały Komitet Rady Ministrów", "reached", rcl_folder(9, "Projekt", filed)
            ),
            legal,
        ),
        consultation=None,
    )
    w = World(
        extractor=FakeTextExtractor(
            page_count=0,
            by_content={b"filed": law(day=filed_day), b"loose": law(day=loose_day)},
        )
    )
    w.add_rcl_project(project)
    w.rcl.files.update({filed.url: b"filed", loose.url: b"loose"})

    w.run(track=False)

    record = w.bill(RCL).analysis
    assert record is not None
    assert record.source_url == (loose.url if loose_day > filed_day else filed.url)
