import datetime as dt
import hashlib

from lexinform.models import (
    CommissionFinding,
    DocumentReview,
    EvidenceCitation,
    ReviewContext,
    ReviewDocument,
)
from lexinform.models.reviews import DocumentRole
from tests.harness import RCL

PROPOSAL = "Art. 1. Proponuje się obniżenie opłaty dla cudzoziemców do 200 PLN."


RESPONSE = "Art. 1. Uwaga uwzględniona. Minister Cyfryzacji obniży opłatę do 200 PLN."


BEFORE = "Art. 1. Cudzoziemiec uiszcza opłatę w wysokości 300 PLN."


AFTER = BEFORE.replace("300", "200")
REPORT = "Sprawozdanie Komisji Nowych Spraw. Komisja wnosi o przyjęcie poprawek.\n\n" + PROPOSAL


def document(
    identity: str, role: DocumentRole, text: str, *, day: int | None = None
) -> ReviewDocument:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return ReviewDocument(
        identity=identity,
        role=role,
        text=text,
        sha256=digest,
        normalized_sha256=digest,
        url="https://example.test/" + identity,
        draft_date=dt.date(2026, 9, day) if day else None,
    )


def citation(identity: str, text: str, locator: str = "Art. 1.") -> EvidenceCitation:
    return EvidenceCitation(document_id=identity, locator=locator, quote=text)


def context() -> ReviewContext:
    before = document("before", "comparison_text", BEFORE, day=1)
    return ReviewContext(
        purpose="commission_review",
        number=RCL,
        title="Projekt ustawy",
        documents=(
            document("proposal", "commission_position", PROPOSAL),
            document("response", "commission_response", RESPONSE),
            before,
            document("after", "project_text", AFTER, day=2),
        ),
        comparison_sha256=before.normalized_sha256,
    )


def finding() -> CommissionFinding:
    return CommissionFinding(
        proposal="Снизить сбор с 300 до 200 PLN",
        respondent="Minister Cyfryzacji",
        outcome="accepted",
        reason="Сбор будет снижен",
        affected_groups=("иностранцы, подающие заявление",),
        practical_impact="Если поправку примут, заявление будет стоить на 100 PLN дешевле.",
        proposal_evidence=citation("proposal", PROPOSAL),
        evidence=citation("response", RESPONSE),
        comparison_evidence=citation("before", BEFORE),
        text_evidence=citation("after", AFTER),
        text_change="В новой версии сбор составляет 200 PLN вместо 300 PLN.",
    )


def review(item: CommissionFinding | None = None) -> DocumentReview:
    return DocumentReview(
        purpose="commission_review",
        complete=True,
        summary="НЕПОДТВЕРЖДЁННОЕ РЕЗЮМЕ",
        commission_findings=(item or finding(),),
        consultation_findings=(),
        effective_rules=(),
        unresolved=(),
    )
