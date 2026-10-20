import pytest

from lexinform.adapters.senat_html import parse_act
from lexinform.commission_reviews import (
    commission_text_matches,
    filed_commission_sources,
    parliamentary_commission_sources,
)
from lexinform.models import (
    Attachment,
    PrintInfo,
    PublicationKind,
    ReviewContext,
    ReviewDocument,
    SenateAct,
    SenateReviewDocument,
    Stage,
    validate_review,
)
from lexinform.services.tracking_result import TrackingResult
from tests.conftest import SENAT_FIXTURES
from tests.fakes import FakeTextExtractor
from tests.harness import RCL, START, World
from tests.scenario.test_commission_reviews import (
    PROPOSAL,
    citation,
    context,
    document,
    finding,
    prepared_world,
    review,
)
from tests.scenario.test_senate import PASSED

REPORT = "Sprawozdanie Komisji Nowych Spraw. Komisja wnosi o przyjęcie poprawek.\n\n" + PROPOSAL


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


def test_joint_senate_reports_and_legal_opinions_are_bound_to_their_own_act() -> None:
    act = parse_act(
        (SENAT_FIXTURES / "ustawa_2169.html").read_text(), "https://www.senat.gov.pl/act"
    )
    assert len(act.review_documents) == 2
    assert {doc.role for doc in act.review_documents} == {
        "commission_report",
        "commission_position",
    }
    report = next(doc for doc in act.review_documents if doc.role == "commission_report")
    assert "Komisji Rodziny" in report.body and "Komisji Spraw Zagranicznych" in report.body
    assert report.url.endswith("849a.pdf")
    assert not any(doc.url.endswith("/849.pdf") for doc in act.review_documents)
    consultation = parse_act(
        (SENAT_FIXTURES / "ustawa_2170.html").read_text(), "https://www.senat.gov.pl/act"
    )
    assert len(consultation.review_documents) == 1
    assert consultation.review_documents[0].role == "commission_position"


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


def test_other_body_cannot_supply_an_answer_or_recommendation_to_this_proposal() -> None:
    ctx = context()
    ctx = ctx.model_copy(
        update={
            "documents": tuple(
                doc.model_copy(
                    update={
                        "body": "Komisja B" if doc.identity == "response" else "Komisja A",
                        "forum": "government",
                    }
                )
                for doc in ctx.documents
            )
        }
    )
    result = validate_review(review(), ctx).commission_findings[0]
    assert result.outcome == "unknown" and result.text_change is None


def test_recommendation_never_becomes_a_chamber_decision_even_if_report_says_accepted() -> None:
    report = document("report", "commission_report", REPORT).model_copy(
        update={"forum": "senate", "body": "Komisja A"}
    )
    ctx = ReviewContext(
        number="3039", title="Projekt", purpose="commission_review", documents=(report,)
    )
    item = finding().model_copy(
        update={
            "proposal_evidence": citation("report", PROPOSAL),
            "evidence": citation("report", REPORT, "Sprawozdanie"),
            "recommendation": "Комиссия предлагает принять поправки",
            "recommendation_evidence": citation("report", REPORT, "Sprawozdanie"),
        }
    )
    result = validate_review(review(item), ctx).commission_findings[0]
    assert result.outcome == "unknown" and result.recommendation
    assert result.text_change is None


@pytest.mark.parametrize(
    "text",
    [
        "Sprawozdanie komisji. Wniosek mniejszości: komisja powinna przyjąć poprawki.",
        "Sprawozdanie komisji. Wnioski mniejszości: wnoszą o przyjęcie poprawek.",
        "Sprawozdanie. Uczestnik posiedzenia proponuje przyjęcie poprawki.",
    ],
)
def test_minority_and_participant_proposals_are_not_committee_recommendations(text: str) -> None:
    doc = document("report", "commission_report", text)
    ctx = ReviewContext(
        number="3039", title="Projekt", purpose="commission_review", documents=(doc,)
    )
    evidence = citation("report", text, "Sprawozdanie")
    item = finding().model_copy(
        update={
            "proposal_evidence": evidence,
            "recommendation": "Принять поправки",
            "recommendation_evidence": evidence,
        }
    )
    checked = validate_review(review(item), ctx).commission_findings[0]
    assert not checked.recommendation and checked.outcome == "unknown"


def test_consultation_attachments_cannot_be_renamed_into_official_senate_opinions() -> None:
    html = (SENAT_FIXTURES / "ustawa_2170.html").read_text().replace("Uwaga 2", "Opinia do ustawy")
    act = parse_act(html, "https://www.senat.gov.pl/act")
    assert len(act.review_documents) == 1
    assert "832o.pdf" in act.review_documents[0].url


def test_same_file_at_another_url_keeps_the_review_memo_key() -> None:
    doc = document("report", "commission_report", REPORT).model_copy(
        update={"body": "Komisja A", "forum": "sejm"}
    )
    ctx = ReviewContext(
        number="3039", title="Projekt", purpose="commission_review", documents=(doc,)
    )
    changed = ctx.model_copy(
        update={
            "documents": (
                doc.model_copy(
                    update={"identity": "new-url", "url": "https://example.test/new-url"}
                ),
            )
        }
    )
    assert ctx.memo_key(10) == changed.memo_key(10)
    other = ctx.model_copy(update={"documents": (doc.model_copy(update={"body": "Komisja B"}),)})
    assert ctx.memo_key(10) != other.memo_key(10)


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


@pytest.mark.parametrize("name", ["krmc-722570", "sejm-1010", "senate-606a"])
def test_real_reports_remain_readable_including_letter_spaced_sejm_heading(name: str) -> None:
    path = SENAT_FIXTURES.parent / "commission_reviews" / f"{name}.json"
    doc = ReviewDocument.model_validate_json(path.read_text())
    assert commission_text_matches(doc.role, doc.text)
    quote = doc.text if name != "sejm-1010" else doc.text.split("Warszawa, dnia", 1)[0]
    evidence = citation(
        doc.identity, quote, "Konkluzja" if name == "krmc-722570" else "Sprawozdanie"
    )
    item = finding().model_copy(
        update={
            "proposal_evidence": evidence,
            "text_change": None,
            "recommendation": "Комиссия рекомендует принять закон",
            "recommendation_evidence": evidence,
        }
    )
    ctx = ReviewContext(
        number="3039", title="Проект", purpose="commission_review", documents=(doc,)
    )
    result = validate_review(review(item), ctx)
    assert result.complete
    assert result.commission_findings[0].recommendation
    assert result.commission_findings[0].outcome == "unknown"


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


def test_filed_opinion_for_another_parent_is_not_selected() -> None:
    attachment = Attachment(
        print_number="opinion", name="opinion.pdf", url="https://api.test/opinion.pdf"
    )
    opinion = PrintInfo(
        term=10,
        number="opinion",
        title="Opinia Komisji Nowej",
        attachments=(attachment,),
        process_prints=("3040",),
    )
    parent = PrintInfo(term=10, number="3039", title="Projekt", additional_prints=(opinion,))
    assert not filed_commission_sources(parent)
