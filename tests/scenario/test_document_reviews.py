import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from lexinform.models import (
    ConsultationFinding,
    DocumentReview,
    DocumentReviewRecord,
    EffectiveRule,
    EvidenceCitation,
    PublicationKind,
    ReviewContext,
    ReviewDocument,
    ReviewWork,
    validate_review,
)
from lexinform.services.analysis import Waiting
from lexinform.services.tracking_result import TrackingResult
from tests.fakes import FakeTextExtractor
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
    w = World(batch=True, workers=workers, batch_kinds=frozenset({"consultation_review"}))
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


def test_late_act_review_has_one_notice_and_grouped_future_rules_without_canonical_repeat() -> None:
    w = World(batch=True, batch_kinds=frozenset({"act_application"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.save_act(10, "3039", act())
    ctx = context(FINAL, role="final_act")
    output = DocumentReview(
        purpose="act_application",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(rule(),),
        unresolved=(),
    )
    w.batch.secondary_script[("3039", "act_application")] = output
    work = ReviewWork(
        memo_key=ctx.memo_key(10),
        purpose="act_application",
        context=ctx,
        since=w.clock.now(),
        baseline_day=dt.date(2026, 9, 10),
    )
    w.repo.save_review_work(10, "3039", (work,))
    reviews = w.container.document_reviews(dry_run=False)
    reviews.advance(TrackingResult(), publish=True)
    assert not w.publisher.texts(PublicationKind.ACT_APPLICATION)
    w.analysis.submit_queued_batches()
    w.batch.resolve()
    w.analysis.collect_batches()
    w.clock.advance(days=30)
    w.repo.restore(w.repo.dump())
    result = TrackingResult()
    reviews.advance(result, publish=True)
    assert result.act_reviews_posted == 1
    assert result.effective_rule_messages == 1
    assert not w.bill("3039").review_work
    text = w.publisher.texts(PublicationKind.EFFECTIVE_RULES)[0]
    assert "24 сент 2026" in text and "не подавайте повторно" in text
    assert "сегодня" not in text
    w.container.poster(dry_run=False).start_run()
    reviews.advance(TrackingResult(), publish=True)
    assert len(w.publisher.texts(PublicationKind.ACT_APPLICATION)) == 1
    assert len(w.publisher.texts(PublicationKind.EFFECTIVE_RULES)) == 1
    assert not w.publisher.texts(PublicationKind.IN_FORCE)


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


def test_review_cost_guard_never_creates_a_synchronous_fallback() -> None:
    w = World(batch=True, batch_kinds=frozenset({"consultation_review"}), max_bill_cost_usd=0.01)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.analysis.start_run()
    bill = w.bill("3039")
    before = bill.status
    assert isinstance(w.analysis.request_document_review(bill, context()), Waiting)
    assert not w.repo.list_queued_batch_intents()
    assert w.analysis.stopped is not None
    assert w.bill("3039").status == before


def test_effective_rule_ownership_survives_payload_retention_and_review_rewording() -> None:
    w = World(batch=True, batch_kinds=frozenset({"act_application"}))
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.repo.save_act(10, "3039", act())
    ctx = context(FINAL, role="final_act")
    output = DocumentReview(
        purpose="act_application",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(rule(),),
        unresolved=(),
    )
    reviewed = validate_review(output, ctx)
    record = DocumentReviewRecord(
        review=reviewed, context=ctx, created_at=w.clock.now(), baseline_day=dt.date(2026, 9, 10)
    )
    w.repo.save_document_review(10, "3039", record)
    w.clock.advance(days=30)
    reviews = w.container.document_reviews(dry_run=False)
    reviews.advance(TrackingResult(), publish=True)
    owner = w.repo.effective_rule_publications(10, "3039", "@test")[0]
    assert owner.status.value == "sent"
    w.repo.prune_history(before=w.clock.now() + dt.timedelta(days=1), memo_before=w.clock.now())
    w.repo.restore(w.repo.dump())
    saved = w.repo.publication_by_id(owner.id or 0)
    assert saved is not None and saved.delivery is None
    changed = reviewed.effective_rules[0].model_copy(
        update={
            "change": "Иное оформление того же изменения",
            "date_kind": "absolute",
            "trigger": "new wording",
        }
    )
    w.repo.save_document_review(
        10,
        "3039",
        record.model_copy(
            update={"review": reviewed.model_copy(update={"effective_rules": (changed,)})}
        ),
    )
    w.container.poster(dry_run=False).start_run()
    reviews.advance(TrackingResult(), publish=True)
    assert len(w.publisher.texts(PublicationKind.EFFECTIVE_RULES)) == 1


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
    w = World(batch=True, batch_kinds=frozenset({"consultation_review"}))
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
    w = World(batch=True, batch_kinds=frozenset({"consultation_review"}))
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


def test_primary_act_notices_are_timely_while_secondary_review_is_waiting() -> None:
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
    assert published.text_pdf_url is not None
    content = b"%PDF-final-review"
    w.gateway.files[published.text_pdf_url] = content
    assert isinstance(w.extractor, FakeTextExtractor)
    w.extractor.by_content[content] = (
        FINAL + "\nArt. 2. Pozostałe przepisy i warunki przejściowe pozostają bez zmian."
    )
    original_rule = rule()
    result_rule = original_rule.model_copy(
        update={
            "evidence": original_rule.evidence.model_copy(update={"document_id": published.eli})
            if original_rule.evidence
            else None,
            "date_evidence": original_rule.date_evidence.model_copy(
                update={"document_id": published.eli}
            )
            if original_rule.date_evidence
            else None,
        }
    )
    w.batch.secondary_script[("3039", "act_application")] = DocumentReview(
        purpose="act_application",
        complete=True,
        summary="",
        consultation_findings=(),
        effective_rules=(result_rule,),
        unresolved=(),
    )
    first = w.run()
    assert first.acts_published == 1 and first.act_reviews_posted == 0
    assert w.bill("3039").review_work and w.bill("3039").status.value == "analyzed"
    notice = w.publication("3039", PublicationKind.ACT_PUBLISHED)
    assert notice is not None and notice.delivery is not None
    frozen = notice.delivery
    w.clock.advance(days=1)
    second = w.run()
    assert second.in_force_posted == 1 and second.act_reviews_posted == 0
    w.batch.resolve()
    third = w.run()
    assert third.act_reviews_posted == 1
    saved_notice = w.publication("3039", PublicationKind.ACT_PUBLISHED)
    assert saved_notice is not None and saved_notice.delivery == frozen
    assert not w.bill("3039").review_work
    final = w.bill("3039").act_application_review
    assert final is not None and final.context.act_eli == published.eli
    assert final.context.documents[0].file_sha256 == hashlib.sha256(content).hexdigest()
    assert "старых заявлений" in w.publisher.texts(PublicationKind.ACT_APPLICATION)[0]


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
