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
    voting: str
    votes_for: str
    votes_against: str
    votes_abstain: str
    not_voting: str
    link_voting_pdf: str
    referred_to_committee: str
    committee_hint: str
    no_print_yet: str
    received: str
    found_in_text: str
    representative: str
    signatories: str
    pre_print_stage: str
    pre_print_note: str
    consultation: str
    consultation_until: str
    consultation_hint: str
    print_assigned: str
    process_withdrawn: str
    link_submission_pdf: str
    act_published_header: str
    journal: str
    published_on: str
    enters_into_force: str
    already_in_force_since: str
    entry_into_force_unknown: str
    partial_vacatio_note: str
    in_force_header: str
    in_force_since: str
    act_summary: str
    link_isap: str
    link_act_pdf: str
    tag_published: str
    tag_in_force: str
    consultation_deadline_header: str
    consultation_days_left: str
    consultation_last_day: str
    tag_consultations: str
    date_format: str = "%Y-%m-%d"
    stage_type_labels: dict[str, str] = field(default_factory=dict)
    senate_position_labels: dict[str, str] = field(default_factory=dict)
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
    link_pdf="PDF",
    link_rcl="RCL",
    link_text_after3="Текст после III чтения",
    joint_prints="Рассматривается совместно с druk",
    current_summary="Суть проекта",
    changes_since_previous="Что изменилось с прошлого раза",
    reanalyzed_note="Текст проекта обновился, анализ выполнен заново.",
    run_report_title="Отчёт о запуске lexinform",
    tag_importance="важность",
    tag_update="обновление",
    voting="Голосование",
    votes_for="за",
    votes_against="против",
    votes_abstain="воздержались",
    not_voting="не голосовали",
    link_voting_pdf="протокол голосования (PDF)",
    referred_to_committee="Направлен в комиссию",
    committee_hint="организации и граждане могут направить в комиссию своё мнение",
    no_print_yet="номер druku ещё не присвоен",
    received="Поступил в Сейм",
    found_in_text="Найден по тексту проекта: название об иностранцах не говорит.",
    representative="представитель",
    signatories="подписали",
    pre_print_stage="проект поступил в Сейм, ожидает присвоения номера druku",
    pre_print_note=(
        "Анализ основан на официальном описании проекта: текст пока доступен только на сайте Сейма."
    ),
    consultation="Общественные консультации",
    consultation_until="до",
    consultation_hint="мнение можно направить через страницу проекта на сайте Сейма",
    print_assigned="Проекту присвоен номер druku",
    process_withdrawn="Проект отозван до присвоения номера druku.",
    link_submission_pdf="PDF проекта (сайт Сейма)",
    act_published_header="Опубликован в Dziennik Ustaw",
    journal="Публикация",
    published_on="опубликован",
    enters_into_force="Вступает в силу",
    already_in_force_since="Уже действует с",
    entry_into_force_unknown="дата вступления в силу пока не указана",
    partial_vacatio_note=(
        "Отдельные положения могут вступать в силу в другие сроки — см. текст закона."
    ),
    in_force_header="С сегодняшнего дня действует",
    in_force_since="вступил в силу",
    act_summary="Суть закона",
    link_isap="ISAP",
    link_act_pdf="Текст закона (PDF)",
    tag_published="опубликован",
    tag_in_force="вступилвсилу",
    consultation_deadline_header="Консультации заканчиваются",
    consultation_days_left="осталось дней",
    consultation_last_day="сегодня последний день",
    tag_consultations="консультации",
    date_format="%d.%m.%Y",
    stage_type_labels={
        "ToPresident": "Закон передан Президенту",
        "PresidentSignature": "✍️ Президент подписал закон",
        "Veto": "⛔ Президент наложил вето",
        "PresidentToTribunal": "⚖️ Президент направил закон в Конституционный трибунал",
        "PublicHearing": (
            "📢 Публичные слушания (wysłuchanie publiczne) — можно подать заявку на участие"
        ),
        "SenatePositionConsideration": "Сейм рассмотрел позицию Сената",
    },
    senate_position_labels={
        "nie wniósł poprawek": "Сенат принял закон без поправок",
        "wniósł poprawki": "Сенат внёс поправки",
        "wniósł poprawkę": "Сенат внёс поправку",
        "odrzucił ustawę": "Сенат отклонил закон",
    },
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
        ApplicantType.PRESIDIUM: "Президиум Сейма",
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
    voting="Vote",
    votes_for="for",
    votes_against="against",
    votes_abstain="abstained",
    not_voting="did not vote",
    link_voting_pdf="voting record (PDF)",
    referred_to_committee="Referred to committee",
    committee_hint="organisations and citizens may send their opinion to the committee",
    no_print_yet="no print number yet",
    received="Received by the Sejm",
    found_in_text="Found by scanning the bill text: the title does not mention foreigners.",
    representative="representative",
    signatories="signed by",
    pre_print_stage="submitted to the Sejm, awaiting a print number",
    pre_print_note=(
        "Analysis based on the official description: the text is only on the Sejm website so far."
    ),
    consultation="Public consultation",
    consultation_until="until",
    consultation_hint="opinions can be submitted via the bill's page on the Sejm website",
    print_assigned="Print number assigned",
    process_withdrawn="The bill was withdrawn before receiving a print number.",
    link_submission_pdf="Bill PDF (Sejm website)",
    act_published_header="Published in Dziennik Ustaw",
    journal="Publication",
    published_on="published",
    enters_into_force="Enters into force",
    already_in_force_since="Already in force since",
    entry_into_force_unknown="entry-into-force date not stated yet",
    partial_vacatio_note="Some provisions may enter into force on other dates — see the act.",
    in_force_header="In force from today",
    in_force_since="in force since",
    act_summary="What the act does",
    link_isap="ISAP",
    link_act_pdf="Act text (PDF)",
    tag_published="published",
    tag_in_force="inforce",
    consultation_deadline_header="Public consultation is closing",
    consultation_days_left="days left",
    consultation_last_day="today is the last day",
    tag_consultations="consultation",
    stage_type_labels={
        "ToPresident": "Sent to the President",
        "PresidentSignature": "✍️ Signed by the President",
        "Veto": "⛔ Vetoed by the President",
        "PresidentToTribunal": "⚖️ Referred by the President to the Constitutional Tribunal",
        "PublicHearing": "📢 Public hearing (wysłuchanie publiczne) — participation requests open",
        "SenatePositionConsideration": "Sejm considered the Senate position",
    },
    senate_position_labels={
        "nie wniósł poprawek": "Senate passed the bill without amendments",
        "wniósł poprawki": "Senate introduced amendments",
        "wniósł poprawkę": "Senate introduced an amendment",
        "odrzucił ustawę": "Senate rejected the bill",
    },
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
        ApplicantType.PRESIDIUM: "Sejm Presidium",
        ApplicantType.CITIZENS: "citizens",
        ApplicantType.COMMITTEE: "committee",
        ApplicantType.UNKNOWN: "unknown",
    },
)

LABELS: dict[str, Labels] = {"ru": RU, "en": EN}


def labels_for(language: str) -> Labels:
    return LABELS.get(language.lower(), EN)
