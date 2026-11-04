import pytest

from lexinform.commission_reviews import (
    filed_commission_sources,
    parliamentary_commission_sources,
)
from lexinform.models import (
    Attachment,
    PrintInfo,
    PublicationKind,
    SenateAct,
    SenateReviewDocument,
    Stage,
)
from lexinform.services.tracking_result import TrackingResult
from tests.commission_cases import PROPOSAL, REPORT, citation, finding, review
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, START, World
from tests.scenario.test_commission_reviews import prepared_world
from tests.scenario.test_senate import PASSED


@pytest.mark.parametrize(
    "name",
    [
        "Stały Komitet Rady Ministrów",
        "Komitet do Spraw Europejskich",
        "Komitet Rady Ministrów do spraw Cyfryzacji",
        "Komisja Wspólna Rządu i Samorządu Terytorialnego",
        "Rada Legislacyjna",
        "Komisja Nowych Spraw",
    ],
)
def test_every_government_review_body_uses_the_shared_queue(name: str) -> None:
    w = prepared_world()
    project = w.rcl.projects[12414100]
    w.rcl.put(
        project.model_copy(
            update={
                "stages": tuple(
                    stage.model_copy(update={"name": name}) if stage.is_legal_commission else stage
                    for stage in project.stages
                )
            }
        )
    )
    assert w.run().ok
    work = w.bill(RCL).review_work
    assert len(work) == 1 and work[0].context is not None
    source = next(doc for doc in work[0].context.documents if doc.identity == "rcl/42")
    assert source.body == name and source.forum == "government"
    assert len(w.batch.submitted) == 1


@pytest.mark.parametrize("forum", ["sejm", "senate"])
@pytest.mark.parametrize("workers", [1, 4])
def test_parliamentary_report_is_automatically_reviewed_as_a_recommendation(
    forum: str, workers: int
) -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"commission_review"}),
        commission_reviews_enabled=True,
        workers=workers,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    assert w.run().ok
    w.clock.advance(days=4)
    url = "https://api.test/committee-report.pdf"
    if forum == "sejm":
        w.set_stages(
            "3039",
            START
            + (
                Stage(
                    stage_name="Sprawozdanie podkomisji",
                    stage_type="CommitteeReport",
                    committee_code="NEW",
                    sub_committee=True,
                    committee_name="Podkomisja Nowych Spraw",
                    report_file=url,
                    print_number="9000",
                    proposal="uchwalić projekt bez poprawek",
                    date=w.clock.now().date(),
                ),
            ),
        )
        w.touch("3039", w.clock.now())
    else:
        w.set_stages("3039", PASSED)
        w.touch("3039", w.clock.now(), passed=True)
        w.gateway.details["3039"] = w.gateway.details["3039"].model_copy(
            update={"title_final": "o cudzoziemcach"}
        )
        assert w.run().ok
    w.gateway.files[url] = b"%PDF-report"
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[b"%PDF-report"] = (
        REPORT + "\n\n" + ("Uzasadnienie proponowanych zmian. " * 40)
    )
    item = finding().model_copy(
        update={
            "proposal_evidence": citation(url, PROPOSAL),
            "evidence": citation(url, REPORT, "Sprawozdanie"),
            "text_change": None,
            "recommendation": "Комиссия рекомендует принять поправки.",
            "recommendation_evidence": citation(url, REPORT, "Sprawozdanie"),
        }
    )
    w.batch.secondary_script[("3039", "commission_review")] = review(item)
    if forum == "senate":
        source = SenateReviewDocument(
            url=url,
            title="Druk nr 100 A",
            body="Komisja Nowych Spraw",
            role="commission_report",
            date=w.clock.now().date(),
        )
        w.senate.put(
            "o cudzoziemcach",
            SenateAct(
                url="https://senat.test/act",
                title="Ustawa",
                received=w.clock.now().date(),
                review_documents=(source,),
            ),
        )
    assert w.run().ok
    assert any(req.question.kind == "commission_review" for req in w.batch.submitted)
    w.repo.restore(w.repo.dump())
    w.batch.resolve()
    assert w.run().ok
    saved = w.bill("3039").commission_review
    assert saved is not None
    checked = saved.review.commission_findings[0]
    assert checked.outcome == "unknown" and checked.recommendation
    assert not checked.text_change and not checked.evidence
    text = w.publisher.texts(PublicationKind.COMMISSION_REVIEW)[0]
    assert "решение палаты этим не подтверждается" in text
    assert "Комиссия рекомендует" in text


def test_identical_joint_sejm_report_preserves_all_committee_names_once() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    stage = Stage(
        stage_name="Sprawozdanie",
        stage_type="CommitteeReport",
        report_file="https://api.test/report.pdf",
        committee_name="Komisja A",
    )
    bill = w.bill("3039").model_copy(
        update={"stages": (stage, stage.model_copy(update={"committee_name": "Komisja B"}))}
    )
    sources = parliamentary_commission_sources(bill)
    assert len(sources) == 1 and sources[0].body == "Komisja A; Komisja B"


def test_existing_reports_on_first_discovery_do_not_start_a_historical_review() -> None:
    w = World(
        batch=True, batch_kinds=frozenset({"commission_review"}), commission_reviews_enabled=True
    )
    stage = Stage(
        stage_name="Sprawozdanie podkomisji",
        stage_type="CommitteeReport",
        sub_committee=True,
        report_file="https://api.test/report.pdf",
        committee_name="Komisja A",
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=START + (stage,))
    assert w.run().ok
    assert not w.batch.submitted and not w.bill("3039").review_work


def test_new_review_retains_full_result_but_publishes_only_new_findings() -> None:
    w = prepared_world()
    w.run()
    w.batch.resolve()
    assert w.run().ok
    first = w.bill(RCL).commission_review
    assert first is not None
    old_item = first.review.commission_findings[0]
    w.clock.advance(days=4)
    project = w.rcl.projects[12414100]
    from_doc = next(
        stage.loose_documents[0]
        for stage in project.stages
        if stage.is_legal_commission and stage.loose_documents
    )
    new_doc = from_doc.model_copy(
        update={"id": 43, "url": from_doc.url.replace("42.", "43."), "name": "Nowe uwagi.docx"}
    )
    w.rcl.put(
        project.model_copy(
            update={
                "modified": w.clock.now().date(),
                "stages": tuple(
                    stage.model_copy(
                        update={
                            "modified": w.clock.now().date(),
                            "loose_documents": (from_doc, new_doc),
                        }
                    )
                    if stage.is_legal_commission
                    else stage
                    for stage in project.stages
                ),
            }
        )
    )
    new_quote = "Art. 2. Proponuje się termin 60 dni na złożenie wniosku przez cudzoziemca."
    w.rcl.files[new_doc.url] = b"new-remarks"
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[b"new-remarks"] = new_quote + "\n" + ("Uzasadnienie nowej uwagi. " * 40)
    new_item = old_item.model_copy(
        update={
            "proposal": "Продлить срок подачи до 60 дней",
            "proposal_evidence": citation("rcl/43", new_quote, "Art. 2."),
        }
    )
    w.batch.secondary_script[(RCL, "commission_review")] = review().model_copy(
        update={"commission_findings": (old_item, new_item)}
    )
    assert w.run().ok
    w.batch.resolve()
    assert w.run().ok
    saved = w.bill(RCL).commission_review
    assert saved is not None and len(saved.review.commission_findings) == 2
    messages = w.publisher.texts(PublicationKind.COMMISSION_REVIEW)
    assert len(messages) == 2
    assert "Продлить срок" in messages[-1] and old_item.proposal not in messages[-1]


def test_filed_committee_opinion_is_reviewed_without_a_committee_report_stage() -> None:
    w = World(
        batch=True, batch_kinds=frozenset({"commission_review"}), commission_reviews_enabled=True
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    original = w.gateway.prints["3039"]
    attachment = Attachment(
        print_number="opinion", name="opinion.pdf", url="https://api.test/opinion.pdf"
    )
    opinion = PrintInfo(
        term=original.term,
        number="opinion",
        title="Opinia Komisji Ustawodawczej do projektu ustawy",
        attachments=(attachment,),
        process_prints=("3039",),
    )
    w.gateway.prints["3039"] = original.model_copy(update={"additional_prints": (opinion,)})
    w.gateway.files[attachment.url] = b"opinion"
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[b"opinion"] = (
        "Opinia Komisji Ustawodawczej\n" + PROPOSAL + "\n" + ("Uzasadnienie uwagi. " * 40)
    )
    item = finding().model_copy(
        update={
            "proposal_evidence": citation("sejm/10/filed/opinion", PROPOSAL),
            "text_change": None,
        }
    )
    w.batch.secondary_script[("3039", "commission_review")] = review(item)
    w.clock.advance(days=4)
    w.touch("3039", w.clock.now())
    assert w.run().ok
    assert w.batch.submitted[-1].question.kind == "commission_review"
    w.repo.restore(w.repo.dump())
    assert w.bill("3039").committee_documents
    w.batch.resolve()
    assert w.run().ok
    saved = w.bill("3039").commission_review
    assert saved is not None and saved.review.commission_findings[0].outcome == "unknown"
    assert "Opinia Komisji Ustawodawczej" in w.publisher.texts(PublicationKind.COMMISSION_REVIEW)[0]


def test_legacy_print_catalog_is_seeded_without_reanalysing_known_opinions() -> None:
    w = World(
        batch=True, batch_kinds=frozenset({"commission_review"}), commission_reviews_enabled=True
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.save_seen_supplements(10, "3039", ("opinion",))
    original = w.gateway.prints["3039"]
    opinion = PrintInfo(
        term=10,
        number="opinion",
        title="Opinia Komisji Nowej",
        attachments=(
            Attachment(
                print_number="opinion", name="opinion.pdf", url="https://api.test/opinion.pdf"
            ),
        ),
    )
    w.gateway.prints["3039"] = original.model_copy(update={"additional_prints": (opinion,)})
    legacy = w.bill("3039").model_copy(update={"committee_documents": None})
    sources = filed_commission_sources(w.gateway.prints["3039"])
    w.repo.save_committee_documents(10, "3039", sources)
    w.container.document_reviews(dry_run=False).observe_commissions([legacy], TrackingResult())
    assert not w.bill("3039").review_work and not w.batch.submitted
