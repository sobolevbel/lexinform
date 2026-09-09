"""Static labels used by the Telegram formatter, keyed by output language.

Bill titles stay in Polish; only the surrounding UI text is localised. The LLM output
language is controlled separately (Settings.output_language) and normally matches.
"""

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
    voting: str
    votes_for: str
    votes_against: str
    votes_abstain: str
    not_voting: str
    link_voting_pdf: str
    referred_to_committee: str
    no_print_yet: str
    received: str
    found_in_text: str
    representative: str
    signatories: str
    pre_print_stage: str
    pre_print_note: str
    consultation: str
    consultation_until: str
    consultation_closed_on: str  # "closed on <date>": a deadline that has passed
    consultation_hint: str
    print_assigned: str
    process_withdrawn: str
    process_discontinued: str  # the term ended with the bill unfinished: it lapsed
    process_carried_over: str  # same, for a citizens' bill: the next Sejm takes it over
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
    tag_term: str  # "#<tag_term><term number>": the Sejm term (kadencja) the bill belongs to
    tag_ukraine: str  # bills about citizens of Ukraine: the channel's largest audience
    consultation_link: str  # text of the link to the Sejm page where opinions are submitted
    action_now: str  # "What you can do now"
    action_send_opinion: str  # "send an opinion via"
    action_consultation_page: str  # link text: the bill's consultation page
    action_committee: str  # "send an opinion to the committee —" (the committee name follows)
    action_before_sitting: str  # "before the sitting of"
    action_hearing: str  # "apply to take part in the public hearing"
    next_step: str  # "What comes next"
    agenda_committee_header: str
    agenda_sejm_header: str
    agenda_item: str
    sejm_sitting: str  # "Sejm sitting no."
    link_video: str
    link_committee: str
    tag_committee_sitting: str
    tag_sejm_sitting: str
    consultation_results_header: str
    consultation_results_hint: str
    # Government projects on RCL (before the Sejm)
    rcl_header: str  # card header: "Government bill (RCL)"
    rcl_ministry: str  # "ministry" (the applicant's name follows)
    rcl_wykaz: str  # "number in the wykaz prac legislacyjnych RM"
    rcl_published: str  # "published on RCL" (date follows)
    rcl_no_stage: str  # stage line when no stage has been reached yet
    rcl_metadata_note: str  # the text could not be read (e.g. legacy .doc): metadata only
    consultation_letter: str  # link text: the consultation letter
    consultation_days_from_letter: str  # "{days} days from the letter"
    consultation_deadline_in_letter: str  # deadline could not be read: "see the letter"
    consultation_email: str  # "comments by e-mail to"
    action_email_ministry: str  # "send comments to {email}"
    action_in_polish: str  # "(in Polish, quoting {wykaz})"
    action_rcl_comment: str  # link text: the comment form on RCL
    rcl_results_hint: str  # opinions and the ministry's answer are on the project page
    rcl_sent_to_sejm: str  # update line: the project went to the Sejm
    link_rcl_project: str
    link_bill_text: str  # "Bill text" (the format follows in brackets)
    link_justification: str
    link_osr: str
    link_wykaz: str
    tag_rcl: str
    event_tags: dict[str, str] = field(default_factory=dict)  # voting, senate, president, ...
    next_step_labels: dict[str, str] = field(default_factory=dict)  # by phase key, see models
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
    voting="Голосование",
    votes_for="за",
    votes_against="против",
    votes_abstain="воздержались",
    not_voting="не голосовали",
    link_voting_pdf="протокол голосования (PDF)",
    referred_to_committee="Направлен в комиссию",
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
    consultation_closed_on="завершились",
    consultation_hint="мнение можно направить через страницу проекта на сайте Сейма",
    print_assigned="Проекту присвоен номер druku",
    process_withdrawn="Проект отозван до присвоения номера druku.",
    process_discontinued=(
        "Каденция Сейма закончилась, проект не был рассмотрен до конца и прекращён"
        " (zasada dyskontynuacji). Чтобы вернуться к нему, проект нужно внести заново в новый Сейм."
    ),
    process_carried_over=(
        "Каденция Сейма закончилась. Гражданский проект переходит к Сейму новой каденции"
        " и получит новый номер druku."
    ),
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
    tag_published="закон",
    tag_in_force="вступилвсилу",
    consultation_deadline_header="Консультации заканчиваются",
    consultation_days_left="осталось дней",
    consultation_last_day="сегодня последний день",
    tag_consultations="консультации",
    tag_term="каденция",
    tag_ukraine="Украина",
    consultation_link="форма для мнений на сайте Сейма",
    action_now="Что можно сделать сейчас",
    action_send_opinion="направить мнение через",
    action_consultation_page="страницу проекта на сайте Сейма",
    action_committee="направить мнение в комиссию —",
    action_before_sitting="до заседания",
    action_hearing="подать заявку на участие в публичных слушаниях",
    next_step="Что дальше",
    agenda_committee_header="Заседание комиссии",
    agenda_sejm_header="В повестке заседания Сейма",
    agenda_item="Пункт повестки",
    sejm_sitting="заседание Сейма №",
    link_video="Трансляция",
    link_committee="Страница комиссии",
    tag_committee_sitting="заседаниекомиссии",
    tag_sejm_sitting="заседаниесейма",
    consultation_results_header="Опубликованы мнения из консультаций",
    consultation_results_hint=(
        "мнения, поданные в ходе общественных консультаций, доступны на странице проекта"
    ),
    rcl_header="Правительственный проект (RCL)",
    rcl_ministry="министерство",
    rcl_wykaz="номер в wykazie prac RM",
    rcl_published="Опубликован на RCL",
    rcl_no_stage="проект опубликован на RCL, работа над ним ещё не началась",
    rcl_metadata_note=(
        "Текст проекта на RCL не удалось прочитать (формат .doc) — анализ по названию и описанию."
    ),
    consultation_letter="письмо о консультациях",
    consultation_days_from_letter="{days} дн. с даты письма",
    consultation_deadline_in_letter="срок указан в письме",
    consultation_email="замечания на e-mail",
    action_email_ministry="направить замечания на {email}",
    action_in_polish="(на польском, с номером {wykaz})",
    action_rcl_comment="оставить комментарий через форму на RCL",
    rcl_results_hint=(
        "поданные мнения (stanowiska) и ответ министерства опубликованы на странице проекта на RCL"
    ),
    rcl_sent_to_sejm="Проект направлен в Сейм — ждём номер druku",
    link_rcl_project="Проект на RCL",
    link_bill_text="Текст проекта",
    link_justification="Uzasadnienie",
    link_osr="OSR",
    link_wykaz="Wykaz prac RM",
    tag_rcl="RCL",
    event_tags={
        "voting": "голосование",
        "senate": "сенат",
        "president": "президент",
        "veto": "вето",
        "amendments": "поправки",
        "withdrawn": "отозван",
        "discontinued": "прекращён",
    },
    next_step_labels={
        "pre_print": "присвоение номера druku, затем I чтение",
        "pre_print_consultation": (
            "консультации до {date}, затем присвоение номера druku и I чтение"
        ),
        "first_reading": "I чтение",
        "first_reading_committee": "I чтение в комиссии — {committee}",
        "first_reading_sitting": "I чтение на заседании Сейма",
        "committee_work": (
            "работа в комиссии — {committee} (sprawozdanie), затем II чтение на заседании Сейма"
        ),
        "second_reading": "II чтение на заседании Сейма",
        "third_reading": "III чтение и голосование в Сейме",
        "senate": "рассмотрение в Сенате (до 30 дней)",
        "senate_amendments": "Сейм рассматривает поправки Сената",
        "president": "подпись Президента (до 21 дня), затем публикация в Dziennik Ustaw",
        "publication": "публикация в Dziennik Ustaw",
        "in_force": "вступление в силу {date}",
        "in_force_unknown": "вступление в силу (дата пока не указана)",
        "veto": "Сейм может отклонить вето (3/5 голосов)",
        "tribunal": "решение Конституционного трибунала",
        "rcl_consultation": (
            "консультации публичные до {date}, затем opiniowanie, комитеты Совета министров,"
            " Rada Ministrów и направление в Сейм"
        ),
        "rcl_opinions": (
            "uzgodnienia и opiniowanie, затем комитеты Совета министров, Rada Ministrów и"
            " направление в Сейм (обычно 3–12 месяцев)"
        ),
        "rcl_committees": (
            "комитеты Совета министров и Komisja Prawnicza, затем Rada Ministrów и направление"
            " в Сейм"
        ),
        "rcl_council": "принятие Радой министров, затем направление в Сейм и номер druku",
        "rcl_to_sejm": "присвоение номера druku в Сейме, затем I чтение",
    },
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
    voting="Vote",
    votes_for="for",
    votes_against="against",
    votes_abstain="abstained",
    not_voting="did not vote",
    link_voting_pdf="voting record (PDF)",
    referred_to_committee="Referred to committee",
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
    consultation_closed_on="closed on",
    consultation_hint="opinions can be submitted via the bill's page on the Sejm website",
    print_assigned="Print number assigned",
    process_withdrawn="The bill was withdrawn before receiving a print number.",
    process_discontinued=(
        "The Sejm term ended before the bill was finished: it lapsed (zasada dyskontynuacji)."
        " To come back, it must be submitted to the new Sejm again."
    ),
    process_carried_over=(
        "The Sejm term ended. As a citizens' bill it is taken over by the new Sejm"
        " and will receive a new print number."
    ),
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
    tag_published="law",
    tag_in_force="inforce",
    consultation_deadline_header="Public consultation is closing",
    consultation_days_left="days left",
    consultation_last_day="today is the last day",
    tag_consultations="consultation",
    tag_term="term",
    tag_ukraine="Ukraine",
    consultation_link="opinion form on the Sejm website",
    action_now="What you can do now",
    action_send_opinion="send an opinion via",
    action_consultation_page="the bill's page on the Sejm website",
    action_committee="send an opinion to the committee —",
    action_before_sitting="before the sitting on",
    action_hearing="apply to take part in the public hearing",
    next_step="What comes next",
    agenda_committee_header="Committee sitting",
    agenda_sejm_header="On the agenda of a Sejm sitting",
    agenda_item="Agenda item",
    sejm_sitting="Sejm sitting no.",
    link_video="Live stream",
    link_committee="Committee page",
    tag_committee_sitting="committeesitting",
    tag_sejm_sitting="sejmsitting",
    consultation_results_header="Consultation opinions published",
    consultation_results_hint=(
        "the opinions submitted during the public consultation are available on the bill's page"
    ),
    rcl_header="Government bill (RCL)",
    rcl_ministry="ministry",
    rcl_wykaz="wykaz prac RM number",
    rcl_published="Published on RCL",
    rcl_no_stage="published on RCL, work has not started yet",
    rcl_metadata_note=(
        "The text on RCL could not be read (.doc format) — analysed from title and description."
    ),
    consultation_letter="consultation letter",
    consultation_days_from_letter="{days} days from the letter",
    consultation_deadline_in_letter="deadline stated in the letter",
    consultation_email="comments by e-mail to",
    action_email_ministry="send comments to {email}",
    action_in_polish="(in Polish, quoting {wykaz})",
    action_rcl_comment="leave a comment through the RCL form",
    rcl_results_hint=(
        "the opinions submitted (stanowiska) and the ministry's answer are on the project page"
        " on RCL"
    ),
    rcl_sent_to_sejm="The bill went to the Sejm — waiting for the print number",
    link_rcl_project="Project on RCL",
    link_bill_text="Bill text",
    link_justification="Uzasadnienie",
    link_osr="OSR",
    link_wykaz="Wykaz prac RM",
    tag_rcl="RCL",
    event_tags={
        "voting": "vote",
        "senate": "senate",
        "president": "president",
        "veto": "veto",
        "amendments": "amendments",
        "withdrawn": "withdrawn",
        "discontinued": "lapsed",
    },
    next_step_labels={
        "pre_print": "print number assignment, then the first reading",
        "pre_print_consultation": (
            "consultation until {date}, then print number assignment and the first reading"
        ),
        "first_reading": "first reading",
        "first_reading_committee": "first reading in committee — {committee}",
        "first_reading_sitting": "first reading at a Sejm sitting",
        "committee_work": (
            "committee work — {committee} (report), then the second reading at a Sejm sitting"
        ),
        "second_reading": "second reading at a Sejm sitting",
        "third_reading": "third reading and the vote in the Sejm",
        "senate": "consideration by the Senate (up to 30 days)",
        "senate_amendments": "the Sejm considers the Senate's amendments",
        "president": (
            "the President's signature (up to 21 days), then publication in Dziennik Ustaw"
        ),
        "publication": "publication in Dziennik Ustaw",
        "in_force": "entry into force on {date}",
        "in_force_unknown": "entry into force (date not stated yet)",
        "veto": "the Sejm may override the veto (3/5 majority)",
        "tribunal": "ruling of the Constitutional Tribunal",
        "rcl_consultation": (
            "public consultation until {date}, then opinions, the committees of the Council of"
            " Ministers, the Council and submission to the Sejm"
        ),
        "rcl_opinions": (
            "inter-ministerial agreement and opinions, then the committees of the Council of"
            " Ministers, the Council and submission to the Sejm (usually 3–12 months)"
        ),
        "rcl_committees": (
            "committees of the Council of Ministers and Komisja Prawnicza, then the Council and"
            " submission to the Sejm"
        ),
        "rcl_council": "adoption by the Council of Ministers, then the Sejm and a print number",
        "rcl_to_sejm": "print number assignment in the Sejm, then the first reading",
    },
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
