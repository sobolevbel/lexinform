"""Static labels used by the Telegram formatter, keyed by output language.

Bill titles stay in Polish; only the surrounding UI text is localised. The LLM output
language is controlled separately (Settings.output_language) and normally matches.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lexinform.models import ApplicantType, Category


@dataclass(frozen=True)
class Labels:
    new_bill_header: str
    update_header: str
    importance: str
    category: str
    about: str
    key_changes: str
    practical_impact: str
    affected: str
    effective_date: str
    effective_date_unknown: str
    stage: str
    applicant: str
    document_date: str
    partial_text_note: str
    new_stages: str
    process_closed: str
    process_passed: str
    link_process: str
    link_pdf: str
    link_rcl: str
    link_text_after3: str
    joint_prints: str
    current_summary: str
    changes_since_previous: str
    reanalyzed_note: str
    run_report_title: str
    tag_importance: str
    tag_update: str
    score_labels: dict[int, str] = field(default_factory=dict)
    category_labels: dict[Category, str] = field(default_factory=dict)
    category_tags: dict[Category, str] = field(default_factory=dict)
    applicant_labels: dict[ApplicantType, str] = field(default_factory=dict)


RU = Labels(
    new_bill_header="Новый законопроект",
    update_header="Обновление",
    importance="Важность",
    category="Категория",
    about="О чём проект",
    key_changes="Ключевые изменения",
    practical_impact="Что это значит на практике",
    affected="Кого касается",
    effective_date="Вступление в силу",
    effective_date_unknown="не указано",
    stage="Стадия",
    applicant="Инициатор",
    document_date="Дата druku",
    partial_text_note="Анализ основан на неполном тексте документа.",
    new_stages="Новые стадии",
    process_closed="Процесс завершён.",
    process_passed="Процесс завершён: закон принят Сеймом.",
    link_process="Ход процесса в Сейме",
    link_pdf="PDF druku",
    link_rcl="RCL",
    link_text_after3="Текст после III чтения",
    joint_prints="Рассматривается совместно с druk",
    current_summary="Суть проекта",
    changes_since_previous="Что изменилось с прошлого раза",
    reanalyzed_note="Текст проекта обновился, анализ выполнен заново.",
    run_report_title="Отчёт о запуске lexinform",
    tag_importance="важность",
    tag_update="обновление",
    score_labels={
        5: "изменения в легализации пребывания",
        4: "работа, Karta Polaka, спецзакон по Украине",
        3: "соцсфера, здравоохранение, образование",
        2: "косвенно касается иностранцев",
        1: "второстепенное упоминание",
    },
    category_labels={
        Category.LEGAL_STAY: "легализация пребывания",
        Category.EMPLOYMENT: "работа и трудоустройство",
        Category.SOCIAL: "соцсфера",
        Category.INDIRECT: "косвенное влияние",
        Category.MARGINAL: "второстепенное",
        Category.NONE: "не относится",
    },
    category_tags={
        Category.LEGAL_STAY: "легализация",
        Category.EMPLOYMENT: "работа",
        Category.SOCIAL: "соцсфера",
        Category.INDIRECT: "косвенно",
        Category.MARGINAL: "второстепенное",
        Category.NONE: "прочее",
    },
    applicant_labels={
        ApplicantType.GOVERNMENT: "правительственный",
        ApplicantType.DEPUTIES: "депутатский",
        ApplicantType.SENATE: "сенатский",
        ApplicantType.PRESIDENT: "президентский",
        ApplicantType.CITIZENS: "гражданский",
        ApplicantType.COMMITTEE: "комиссионный",
        ApplicantType.UNKNOWN: "не определён",
    },
)

EN = Labels(
    new_bill_header="New bill",
    update_header="Update",
    importance="Importance",
    category="Category",
    about="What it is about",
    key_changes="Key changes",
    practical_impact="What it means in practice",
    affected="Who is affected",
    effective_date="Entry into force",
    effective_date_unknown="not specified",
    stage="Stage",
    applicant="Submitted by",
    document_date="Print date",
    partial_text_note="The analysis is based on a partial text of the document.",
    new_stages="New stages",
    process_closed="Process closed.",
    process_passed="Process closed: the bill was passed by the Sejm.",
    link_process="Legislative process",
    link_pdf="Print PDF",
    link_rcl="RCL",
    link_text_after3="Text after 3rd reading",
    joint_prints="Considered jointly with print",
    current_summary="Summary",
    changes_since_previous="What changed since the previous version",
    reanalyzed_note="The bill text was updated; the analysis was redone.",
    run_report_title="lexinform run report",
    tag_importance="importance",
    tag_update="update",
    score_labels={
        5: "changes to legalization of stay",
        4: "employment, Karta Polaka, Ukraine special act",
        3: "social benefits, healthcare, education",
        2: "indirect impact on foreigners",
        1: "marginal mention",
    },
    category_labels={
        Category.LEGAL_STAY: "legal stay",
        Category.EMPLOYMENT: "employment",
        Category.SOCIAL: "social",
        Category.INDIRECT: "indirect",
        Category.MARGINAL: "marginal",
        Category.NONE: "not related",
    },
    category_tags={
        Category.LEGAL_STAY: "legalstay",
        Category.EMPLOYMENT: "employment",
        Category.SOCIAL: "social",
        Category.INDIRECT: "indirect",
        Category.MARGINAL: "marginal",
        Category.NONE: "other",
    },
    applicant_labels={
        ApplicantType.GOVERNMENT: "government",
        ApplicantType.DEPUTIES: "deputies",
        ApplicantType.SENATE: "Senate",
        ApplicantType.PRESIDENT: "President",
        ApplicantType.CITIZENS: "citizens",
        ApplicantType.COMMITTEE: "committee",
        ApplicantType.UNKNOWN: "unknown",
    },
)

LABELS: dict[str, Labels] = {"ru": RU, "en": EN}


def labels_for(language: str) -> Labels:
    return LABELS.get(language.lower(), EN)
