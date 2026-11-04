import datetime as dt
import hashlib

from lexinform.models import (
    ConsultationFinding,
    DocumentReview,
    EffectiveRule,
    EvidenceCitation,
    ReviewContext,
    ReviewDocument,
)

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
