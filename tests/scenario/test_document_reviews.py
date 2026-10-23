import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from lexinform.models import (
    BatchRequest,
    ConsultationFinding,
    DeliveryPlan,
    DocumentReview,
    DocumentReviewRecord,
    EffectiveRule,
    EvidenceCitation,
    Publication,
    PublicationKind,
    PublicationStatus,
    ReviewContext,
    ReviewDocument,
    ReviewWork,
    validate_review,
)
from lexinform.services.analysis import SECONDARY_BATCH_SUBMISSIONS, Waiting
from lexinform.services.tracking_result import TrackingResult
from tests.harness import World, act

RESPONSE = "Pkt 1. Uwaga uwzględniona. Opłata wynosi 200 PLN, nie 300 PLN."
FINAL = (
    "Art. 1. Cudzoziemcy nie składają nowego wniosku, jeżeli złożyli go przed 1 września 2026 r. "
    "Art. 15. Art. 1 wchodzi w życie po upływie 14 dni od dnia ogłoszenia."
)


def context(text: str = RESPONSE, *, role: str = "response") -> ReviewContext:
    document = ReviewDocument.model_validate(
        dict(
            identity="doc/1",
            url="https://example.test/official",
            role=role,
            text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            normalized_sha256=hashlib.sha256(" ".join(text.split()).encode()).hexdigest(),
        )
    )
    return ReviewContext(
        number="3039",
        title="Projekt ustawy o cudzoziemcach",
        purpose="act_application" if role == "final_act" else "consultation_review",
        documents=(document,),
        act_eli="DU/2026/1099" if role == "final_act" else None,
        promulgation_date=dt.date(2026, 9, 9),
        canonical_date=dt.date(2026, 9, 20),
    )


def finding(
    *, quote: str = "Pkt 1. Uwaga uwzględniona.", outcome: str = "accepted"
) -> ConsultationFinding:
    return ConsultationFinding.model_validate(
        dict(
            proposal="Оплата 200 PLN",
            respondent="Ministerstwo",
            outcome=outcome,
            reason="Нужен меньший сбор",
            affected_groups=["заявители"],
            evidence=dict(document_id="doc/1", locator="Pkt 1.", quote=quote),
            text_change="Изменение вошло в закон",
            text_evidence=None,
        )
    )


def review(item: ConsultationFinding) -> DocumentReview:
    return DocumentReview(
        purpose="consultation_review",
        complete=True,
        summary="Итог",
        consultation_findings=(item,),
        effective_rules=(),
        unresolved=(),
    )


def rule(day: dt.date | None = None) -> EffectiveRule:
    return EffectiveRule(
        locator="Art. 1",
        change="Для старых заявлений новый документ не нужен.",
        affected_groups=("иностранцы со старыми заявлениями",),
        conditions=("заявление подано до 1 сентября 2026",),
        exceptions=("новые заявления исключены",),
        old_applications="не подавайте повторно",
        documents=(),
        date_kind="relative",
        date=day,
        days_after_promulgation=14,
        trigger="ogłoszenie",
        evidence=EvidenceCitation(
            document_id="doc/1", locator="Art. 1.", quote=FINAL.split("Art. 15.")[0]
        ),
        date_evidence=EvidenceCitation(
            document_id="doc/1", locator="Art. 15.", quote="Art. 15." + FINAL.split("Art. 15.")[1]
        ),
    )


@pytest.mark.parametrize(
    ("text", "outcome", "expected"),
    [
        (RESPONSE, "accepted", "accepted"),
        ("Pkt 1. Uwaga nieuwzględniona.", "accepted", "unknown"),
        ("Pkt 1. Uwaga nie uwzględniona.", "accepted", "unknown"),
        ("Pkt 1. Uwaga odrzucona.", "rejected", "rejected"),
        ("Pkt 1. Uwaga uwzględniona częściowo.", "partial", "partial"),
        ("Pkt 1. Uwaga uwzględniona częściowo.", "accepted", "unknown"),
        ("Pkt 1. Uwaga przyjęta. Nie dotyczy cudzoziemców.", "accepted", "accepted"),
    ],
)
def test_review_requires_matching_response_outcome(text: str, outcome: str, expected: str) -> None:
    answer = validate_review(review(finding(quote=text, outcome=outcome)), context(text))
    assert answer.consultation_findings[0].outcome == expected
    assert answer.consultation_findings[0].text_change is None
    if expected == "unknown":
        assert not answer.complete
        assert not answer.consultation_findings[0].affected_groups
        assert not answer.consultation_findings[0].reason


@pytest.mark.parametrize("role", ["position", "project_text"])
def test_participant_position_never_proves_acceptance(role: str) -> None:
    answer = validate_review(review(finding()), context(role=role))
    assert answer.consultation_findings[0].outcome == "unknown"
    assert answer.consultation_findings[0].evidence is None


@pytest.mark.parametrize("replacement", ["300 PLN", "Nie uwzględniono", "Pkt 11.", "inne osoby"])
def test_changed_quote_cannot_match_the_previous_version(replacement: str) -> None:
    changed = finding().model_copy(
        update={
            "evidence": EvidenceCitation(
                document_id="doc/1", locator="Pkt 1.", quote="Pkt 1. " + replacement
            )
        }
    )
    answer = validate_review(review(changed), context())
    assert answer.consultation_findings[0].outcome == "unknown"


def test_relative_day_is_computed_only_from_confirmed_final_act_and_origin() -> None:
    output = DocumentReview(
        purpose="act_application",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(rule(),),
        unresolved=(),
    )
    answer = validate_review(output, context(FINAL, role="final_act"))
    assert answer.effective_rules[0].date == dt.date(2026, 9, 24)
    assert answer.effective_rules[0].conditions == rule().conditions
    missing_origin = context(FINAL, role="final_act").model_copy(update={"promulgation_date": None})
    assert validate_review(output, missing_origin).effective_rules[0].date is None
    draft = context(FINAL, role="final_act").model_copy(
        update={"documents": (context(FINAL).documents[0],)}
    )
    assert not validate_review(output, draft).effective_rules


@pytest.mark.parametrize("workers", [1, 4])
def test_review_batch_survives_restore_unknown_submission_and_preserves_bill_status(
    workers: int,
) -> None:
    w = World(
        batch=True,
        workers=workers,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    before = w.bill("3039").status
    ctx = context()
    w.batch.secondary_script[("3039", "consultation_review")] = review(finding())
    assert isinstance(w.analysis.request_document_review(w.bill("3039"), ctx), Waiting)
    intent = w.repo.list_queued_batch_intents()[0]
    w.repo.mark_batch_intents_submitting([intent.request.custom_id])
    w.clock.advance(hours=48)
    w.repo.restore(w.repo.dump())
    w.analysis.start_run()
    assert isinstance(w.analysis.request_document_review(w.bill("3039"), ctx), Waiting)
    assert not w.batch.submitted
    w.repo.mark_batch_intents_queued([intent.request.custom_id])
    w.analysis.submit_queued_batches()
    w.batch.resolve()
    w.analysis.collect_batches()
    w.repo.restore(w.repo.dump())
    w.analysis.start_run()
    result = w.analysis.request_document_review(w.bill("3039"), ctx)
    assert isinstance(result, DocumentReviewRecord)
    assert result.input_tokens == 0
    assert result.review.consultation_findings[0].outcome == "accepted"
    assert result.context.documents[0].sha256 == ctx.documents[0].sha256
    assert w.bill("3039").status == before
    assert len(w.batch.submitted) == 1
    assert not w.llm.joint_contexts


def test_legacy_act_review_work_is_preserved_without_new_analysis_or_posts() -> None:
    w = World(batch=True, batch_kinds=frozenset({"act_application"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.save_act(10, "3039", act())
    ctx = context(FINAL, role="final_act")
    work = ReviewWork(
        memo_key=ctx.memo_key(10),
        purpose="act_application",
        context=ctx,
        since=w.clock.now(),
        baseline_day=dt.date(2026, 9, 10),
    )
    w.repo.save_review_work(10, "3039", (work,))
    submitted = list(w.batch.submitted)
    w.clock.advance(days=30)
    w.repo.restore(w.repo.dump())
    result = TrackingResult()
    w.container.document_reviews(dry_run=False).advance(result, publish=True)

    assert w.bill("3039").review_work == (work,)
    assert w.batch.submitted == submitted
    assert not w.repo.list_queued_batch_intents()
    assert result.act_reviews_posted == result.effective_rule_messages == 0
    assert not w.publisher.texts(PublicationKind.ACT_APPLICATION)
    assert not w.publisher.texts(PublicationKind.EFFECTIVE_RULES)


def test_manually_labelled_real_rcl_response_rows_and_mutations() -> None:
    data = json.loads(Path("tests/fixtures/message_usefulness/review-evidence.json").read_text())
    for item in data:
        ctx = context(item["quote"])
        evidence = EvidenceCitation(
            document_id="doc/1", locator=item["locator"], quote=item["quote"]
        )
        observed = finding(outcome=item["outcome"]).model_copy(update={"evidence": evidence})
        assert (
            validate_review(review(observed), ctx).consultation_findings[0].outcome
            == item["outcome"]
        )
        for old, new in [
            ("200", "300"),
            ("Art.", "Pkt."),
            ("ust.", "pkt."),
            ("wyrobu", "cudzoziemca"),
            ("uwzględniona.", "nie uwzględniona."),
        ]:
            altered = item["quote"].replace(old, new)
            if altered == item["quote"]:
                continue
            changed = observed.model_copy(
                update={"evidence": evidence.model_copy(update={"quote": altered})}
            )
            assert (
                validate_review(review(changed), ctx).consultation_findings[0].outcome == "unknown"
            )


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        (
            "Art. 15. Ustawa wchodzi w życie po upływie 2 miesięcy od dnia ogłoszenia.",
            dt.date(2026, 12, 1),
        ),
        (
            "Art. 15. Ustawa wchodzi w życie po upływie 2 tygodni od dnia ogłoszenia.",
            dt.date(2026, 10, 15),
        ),
        (
            "Art. 15. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia, z wyjątkiem art. 1.",
            None,
        ),
        ("Art. 15. Ustawa wchodzi w życie z dniem wydania rozporządzenia.", None),
    ],
)
def test_relative_dates_use_calendar_rules_and_never_guess_a_conditional_trigger(
    quote: str, expected: dt.date | None
) -> None:
    ctx = context(FINAL + quote, role="final_act").model_copy(
        update={"promulgation_date": dt.date(2026, 9, 30), "canonical_date": None}
    )
    item = rule().model_copy(
        update={
            "days_after_promulgation": None,
            "date_evidence": EvidenceCitation(document_id="doc/1", locator="Art. 15.", quote=quote),
        }
    )
    output = DocumentReview(
        purpose="act_application",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(item,),
        unresolved=(),
    )
    assert validate_review(output, ctx).effective_rules[0].date == expected


def test_a_quote_cannot_hide_exceptions_after_the_general_effective_clause() -> None:
    general = "Art. 15. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia"
    source = (
        FINAL.split("Art. 15.")[0]
        + general
        + ", z wyjątkiem art. 1, który zaczyna obowiązywać później."
    )
    item = rule().model_copy(
        update={
            "date_evidence": EvidenceCitation(
                document_id="doc/1",
                locator="Art. 15.",
                quote=general,
            )
        }
    )
    output = DocumentReview(
        purpose="act_application",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(item,),
        unresolved=(),
    )
    answer = validate_review(output, context(source, role="final_act"))
    assert answer.effective_rules[0].date is None
    assert not answer.complete


@pytest.mark.parametrize(("limit", "estimate"), [(0.01, 0.1), (2.0, 4.4416)])
def test_review_cost_guard_never_creates_a_synchronous_fallback(
    monkeypatch: pytest.MonkeyPatch, limit: float, estimate: float
) -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        max_bill_cost_usd=limit,
        max_run_cost_usd=15.0,
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.analysis.start_run()
    prepare = w.batch.prepare_request

    def prepare_review(request: BatchRequest) -> BatchRequest:
        return prepare(request).model_copy(update={"estimated_cost_usd": estimate})

    monkeypatch.setattr(w.batch, "prepare_request", prepare_review)
    bill = w.bill("3039")
    before = bill.status
    assert isinstance(w.analysis.request_document_review(bill, context()), Waiting)
    assert not w.repo.list_queued_batch_intents()
    assert w.analysis.stopped == (
        f"per-bill cost limit exceeded for 3039 (request ≈${estimate:.2f} > ${limit:.2f}); "
        "document review remains pending"
    )
    assert w.bill("3039").status == before


def test_review_reports_the_run_limit_when_reservation_fails() -> None:
    w = World(
        batch=True,
        max_bill_cost_usd=2.0,
        max_run_cost_usd=0.01,
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.analysis.start_run()

    assert isinstance(w.analysis.request_document_review(w.bill("3039"), context()), Waiting)

    assert not w.repo.list_queued_batch_intents()
    assert w.analysis.stopped == (
        "run cost limit reached (spent/reserved ≈$0.00, next request exceeds $0.01); "
        "document review waits for the next run"
    )


def test_legacy_queued_act_analysis_is_not_submitted() -> None:
    w = World(batch=True, batch_kinds=frozenset({"act_application"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.analysis.start_run()
    assert isinstance(
        w.analysis.request_document_review(w.bill("3039"), context(FINAL, role="final_act")),
        Waiting,
    )
    pending = w.repo.list_queued_batch_intents()
    assert pending
    submitted = list(w.batch.submitted)

    w.analysis.submit_queued_batches()

    assert w.repo.list_queued_batch_intents() == pending
    assert w.batch.submitted == submitted


@pytest.mark.parametrize("kind", [PublicationKind.ACT_APPLICATION, PublicationKind.EFFECTIVE_RULES])
@pytest.mark.parametrize(
    "status", [PublicationStatus.QUEUED, PublicationStatus.FAILED, PublicationStatus.UNKNOWN]
)
def test_legacy_act_review_deliveries_are_stopped_without_changing_payloads(
    kind: PublicationKind,
    status: PublicationStatus,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.save_act(10, "3039", act())
    record = DocumentReviewRecord(
        review=DocumentReview(
            purpose="act_application",
            complete=True,
            summary="",
            consultation_findings=(),
            effective_rules=(rule(),),
            unresolved=(),
        ),
        context=context(FINAL, role="final_act"),
        created_at=w.clock.now(),
    )
    w.repo.save_document_review(10, "3039", record)
    poster = w.container.poster(dry_run=False)
    pub = poster.prepare_message(w.bill("3039"), kind, ref="legacy")
    assert pub.id is not None
    w.repo.mark_publication(pub.id, status, count_attempt=False)
    saved = w.repo.publication_by_id(pub.id)
    assert saved is not None

    assert not poster.deliver(saved)
    current = w.repo.publication_by_id(pub.id)
    assert current is not None and current.delivery == saved.delivery
    assert current.status is (
        PublicationStatus.UNKNOWN
        if status is PublicationStatus.UNKNOWN
        else PublicationStatus.SKIPPED
    )
    assert w.bill("3039").act_application_review == record
    assert not w.publisher.texts(kind)


@pytest.mark.parametrize("kind", [PublicationKind.ACT_APPLICATION, PublicationKind.EFFECTIVE_RULES])
def test_legacy_act_review_continuation_is_not_sent_after_confirmed_root(
    kind: PublicationKind,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    poster = w.container.poster(dry_run=False)
    root_id = poster.record(w.bill("3039"), kind, PublicationStatus.SENT, ref="legacy")
    child_id = w.repo.create_publication(
        Publication(
            term=10,
            number="3039",
            channel_id="@test",
            kind=PublicationKind.MESSAGE_PART,
            status=PublicationStatus.QUEUED,
            ref="legacy/2",
            created_at=w.clock.now(),
            delivery=DeliveryPlan(
                bill_json=w.bill("3039").model_dump_json(),
                rendered_text="Продолжение разбора",
                continuation_root=root_id,
                continuation_parent=root_id,
                continuation_index=2,
            ),
        )
    )
    child = w.repo.publication_by_id(child_id)
    assert child is not None

    assert not poster.deliver(child)
    saved = w.repo.publication_by_id(child_id)
    assert saved is not None and saved.status is PublicationStatus.SKIPPED
    assert saved.delivery == child.delivery


def test_manually_labelled_final_act_special_dates_and_material_quote_mutations() -> None:
    fixtures = json.loads(
        Path("tests/fixtures/message_usefulness/act-rule-evidence.json").read_text()
    )
    for item in fixtures:
        ctx = context(item["quote"], role="final_act")
        citation = EvidenceCitation(
            document_id="doc/1", locator=item["locator"], quote=item["quote"]
        )
        effective = rule().model_copy(
            update={
                "locator": item["locator"],
                "evidence": citation,
                "date_evidence": citation,
                "date_kind": "absolute",
                "date": dt.date.fromisoformat(item["date"]),
                "days_after_promulgation": None,
            }
        )
        output = DocumentReview(
            purpose="act_application",
            complete=True,
            summary="",
            consultation_findings=(),
            effective_rules=(effective,),
            unresolved=(),
        )
        assert validate_review(output, ctx).effective_rules[0].date == effective.date
        for prefix in (
            "Nie stosuje się. ",
            "Kwota 200 PLN. ",
            "Tylko nowe wnioski. ",
            "Art. 15 ust. 2. ",
            "Data 2026-12-01. ",
        ):
            altered = citation.model_copy(update={"quote": prefix + item["quote"]})
            wrong = effective.model_copy(update={"date_evidence": altered})
            assert (
                validate_review(output.model_copy(update={"effective_rules": (wrong,)}), ctx)
                .effective_rules[0]
                .date
                is None
            )


@pytest.mark.parametrize(
    "quote",
    [
        "Pkt 1. Uwaga przyjęta do wiadomości.",
        "Pkt 1. Proponuje się, aby uwaga została uwzględniona.",
    ],
)
def test_receipt_or_a_proposal_is_not_a_confirmed_acceptance(quote: str) -> None:
    assert (
        validate_review(review(finding(quote=quote)), context(quote))
        .consultation_findings[0]
        .outcome
        == "unknown"
    )


def test_retained_review_reuses_content_even_after_generic_memo_retention() -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    ctx = context()
    record = DocumentReviewRecord(
        review=validate_review(review(finding()), ctx),
        context=ctx,
        created_at=w.clock.now(),
        input_tokens=123,
    )
    w.repo.save_document_review(10, "3039", record)
    w.repo.restore(w.repo.dump())
    answer = w.analysis.request_document_review(w.bill("3039"), ctx)
    assert isinstance(answer, DocumentReviewRecord) and answer.input_tokens == 0
    assert not w.repo.list_queued_batch_intents()


def test_government_review_identity_survives_term_rollover_without_crossing_print_terms() -> None:
    gov = context().model_copy(update={"number": "RCL/12403400"})
    assert gov.memo_key(10) == gov.memo_key(11)
    assert context().memo_key(10) != context().memo_key(11)
    register = context().model_copy(update={"number": "WPL/UD408"})
    assert register.memo_key(10) != register.memo_key(11)


def test_review_context_cannot_cross_bill_identity() -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    with pytest.raises(ValueError, match="different bill"):
        w.analysis.request_document_review(
            w.bill("3039"), context().model_copy(update={"number": "3040"})
        )
    assert not w.repo.list_queued_batch_intents()


def test_citation_cannot_strip_a_negation_or_quote_only_a_proposal_column() -> None:
    denied = "Pkt 1. Uwaga nieuwzględniona."
    quoted = finding().model_copy(
        update={
            "evidence": EvidenceCitation(
                document_id="doc/1",
                locator="uwzględniona",
                quote="uwzględniona",
            )
        }
    )
    assert (
        validate_review(review(quoted), context(denied)).consultation_findings[0].outcome
        == "unknown"
    )
    source = "1\tArt. 1\tPropozycja: uwaga uwzględniona.\tUwaga nieuwzględniona."
    truncated_row = finding().model_copy(
        update={
            "evidence": EvidenceCitation(
                document_id="doc/1",
                locator="1\tArt. 1",
                quote=source.rsplit("\t", 1)[0],
            )
        }
    )
    assert (
        validate_review(review(truncated_row), context(source)).consultation_findings[0].outcome
        == "unknown"
    )


def test_multiline_table_proposal_cannot_stand_in_for_the_response() -> None:
    proposal = "1\tArt. 1\tPropozycja: uwaga uwzględniona."
    source = (
        proposal
        + "\nDalsze uzasadnienie uczestnika.\tUwaga nieuwzględniona.\n2\tArt. 2\tInna uwaga\tUwaga odrzucona."
    )
    partial_row = finding().model_copy(
        update={
            "evidence": EvidenceCitation(
                document_id="doc/1",
                locator="1\tArt. 1",
                quote=proposal,
            )
        }
    )
    assert (
        validate_review(review(partial_row), context(source)).consultation_findings[0].outcome
        == "unknown"
    )


def test_real_final_act_transition_keeps_both_old_proceeding_conditions() -> None:
    data = json.loads(
        Path("tests/fixtures/message_usefulness/act-application-evidence.json").read_text()
    )[0]
    ctx = context(data["quote"], role="final_act")
    citation = EvidenceCitation(document_id="doc/1", locator=data["locator"], quote=data["quote"])
    effective = EffectiveRule.model_validate(
        {
            **{
                key: data[key]
                for key in (
                    "locator",
                    "change",
                    "affected_groups",
                    "conditions",
                    "exceptions",
                    "old_applications",
                    "date_kind",
                    "date",
                    "trigger",
                )
            },
            "documents": [],
            "days_after_promulgation": None,
            "evidence": citation,
            "date_evidence": citation,
        }
    )
    output = DocumentReview(
        purpose="act_application",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(effective,),
        unresolved=(),
    )
    validated = validate_review(output, ctx)
    assert validated.effective_rules[0].conditions == effective.conditions
    assert len(validated.effective_rules[0].conditions) == 2
    assert validated.effective_rules[0].date is None
    assert validated.effective_rules[0].date_kind == "conditional"
    wrong_quote = citation.model_copy(
        update={"quote": citation.quote.replace("niezakończonych", "zakończonych")}
    )
    wrong = effective.model_copy(update={"evidence": wrong_quote})
    assert not validate_review(
        output.model_copy(update={"effective_rules": (wrong,)}), ctx
    ).effective_rules


def test_primary_act_notices_continue_without_secondary_act_review() -> None:
    w = World(batch=True, batch_kinds=frozenset({"act_application"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.publish_act("3039")
    today = w.clock.now().date()
    published = act(
        promulgation_date=today,
        entry_into_force=today + dt.timedelta(days=1),
        fetched_at=w.clock.now(),
    )
    w.gateway.acts[published.eli] = published

    first = w.run()
    assert first.acts_published == 1 and first.act_reviews_posted == 0
    assert not w.bill("3039").review_work
    w.repo.save_document_review(
        10,
        "3039",
        DocumentReviewRecord(
            review=DocumentReview(
                purpose="act_application",
                complete=True,
                summary="",
                consultation_findings=(),
                effective_rules=(rule(),),
                unresolved=(),
            ),
            context=context(FINAL, role="final_act"),
            created_at=w.clock.now(),
        ),
    )
    w.clock.advance(days=1)
    second = w.run()
    assert second.in_force_posted == 1 and second.act_reviews_posted == 0
    assert "Как применяется окончательный акт" not in w.publisher.texts(PublicationKind.IN_FORCE)[0]
    assert rule().change not in w.publisher.texts(PublicationKind.IN_FORCE)[0]
    assert not w.bill("3039").review_work
    assert not w.publisher.texts(PublicationKind.ACT_APPLICATION)


def test_conflicting_general_date_is_not_a_confirmed_special_date() -> None:
    quote = "Art. 15. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia."
    ctx = context(FINAL + quote, role="final_act")
    effective = rule().model_copy(
        update={
            "date_evidence": EvidenceCitation(
                document_id="doc/1",
                locator="Art. 15.",
                quote=quote,
            )
        }
    )
    output = DocumentReview(
        purpose="act_application",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(effective,),
        unresolved=(),
    )
    assert validate_review(output, ctx).effective_rules[0].date is None


def test_a_review_failed_at_every_submission_keeps_no_documents_and_is_not_retried() -> None:
    w = World(
        batch=True,
        batch_kinds=frozenset({"consultation_review"}),
        consultation_reviews_enabled=True,
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    ctx = context()
    work = ReviewWork(
        memo_key=ctx.memo_key(10),
        purpose="consultation_review",
        context=ctx,
        source_key="review-input:3039",
        since=w.clock.now(),
        baseline_day=dt.date(2026, 9, 10),
    )
    w.repo.save_review_work(10, "3039", (work,))
    w.batch.secondary_script[("3039", "consultation_review")] = RuntimeError(
        "response is incomplete"
    )
    reviews = w.container.document_reviews(dry_run=False)
    for _ in range(SECONDARY_BATCH_SUBMISSIONS):
        w.analysis.start_run()
        reviews.advance(TrackingResult(), publish=True)
        w.analysis.submit_queued_batches()
        w.batch.resolve()
        w.analysis.collect_batches()
    submitted = len(w.batch.submitted)

    result = TrackingResult()
    w.analysis.start_run()
    reviews.advance(result, publish=True)
    reviews.advance(result, publish=True)

    (left,) = w.bill("3039").review_work
    assert left.abandoned and left.source_key == work.source_key
    assert submitted == SECONDARY_BATCH_SUBMISSIONS == len(w.batch.submitted)
    assert not result.partial_errors
