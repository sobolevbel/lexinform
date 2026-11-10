import datetime as dt
import json
from pathlib import Path

import pytest

from lexinform.models import (
    DocumentReview,
    EffectiveRule,
    EvidenceCitation,
    validate_review,
)
from tests.document_review_cases import FINAL, RESPONSE, context, finding, review, rule


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


def test_government_review_identity_survives_term_rollover_without_crossing_print_terms() -> None:
    gov = context().model_copy(update={"number": "RCL/12403400"})
    assert gov.memo_key(10) == gov.memo_key(11)
    assert context().memo_key(10) != context().memo_key(11)
    register = context().model_copy(update={"number": "WPL/UD408"})
    assert register.memo_key(10) != register.memo_key(11)


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
