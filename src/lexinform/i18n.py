"""Static labels used by the Telegram formatter, keyed by output language.

Bill titles stay in Polish; only the surrounding UI text is localised. The LLM output
language is controlled separately (Settings.output_language) and normally matches.
"""

from dataclasses import dataclass, field

from lexinform.models import ApplicantType, Category


@dataclass(frozen=True)
class Labels:
    new_bill_header: str
    finished_bill_header: str  # the card of a bill whose road had already ended
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
    print_number: str  # what a print (druk) is called before its number
    partial_text_note: str
    new_stages: str
    reading_stage: str  # "{numeral} чтение …": a reading that has happened
    process_closed: str
    process_passed: str
    process_not_enacted: str  # the process ended without a law, and how is not recorded
    process_veto_sustained: str
    stage_veto_sustained: str  # the `End` node of a bill the veto killed
    link_process: str
    link_pdf: str
    link_rcl: str
    link_text_after3: str
    joint_prints: str
    # A bill considered jointly with one that already has a card: header of the reply under that
    # card and the note that the group is followed there ("{numbers}" = the other prints)
    joint_bill_header: str
    joint_bill_note: str
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
    consultation_closed_on: str
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
    in_force_header: str  # the act enters into force today
    in_force_header_dated: str  # ... and when a run was missed and it was earlier
    in_force_since: str
    act_summary: str
    link_isap: str
    link_act_pdf: str
    tag_published: str
    tag_in_force: str
    consultation_deadline_header: str
    consultation_days_left: str
    consultation_last_day: str
    tag_consultations: str  # an open consultation: a reader can still send an opinion
    tag_consultation_results: str  # the opinions received, once the window has shut
    tag_term: str  # "#<tag_term><term number>": the Sejm term (kadencja) the bill belongs to
    tag_ukraine: str  # bills about citizens of Ukraine: the channel's largest audience
    consultation_link: str  # the Sejm page of a bill under consultation (it carries no form)
    consultation_page: str  # the same page, named neutrally: the consultation is over
    action_now: str
    action_senate: str
    path: str  # the one-line map of the process with the current step marked
    action_send_opinion: str
    action_consultation_page: str
    action_committee: str
    action_before_sitting: str
    action_hearing: str
    next_step: str
    agenda_committee_header: str
    agenda_sejm_header: str
    agenda_item: str
    sitting_moved_from: str  # the same sitting was announced for another day before
    sejm_sitting: str
    link_video: str
    link_committee: str
    link_senate_bills: str  # the Senate's listing of the laws the Sejm has passed
    tag_committee_sitting: str
    tag_sejm_sitting: str
    consultation_results_header: str
    consultation_results_hint: str
    # Government projects on RCL (before the Sejm)
    rcl_header: str
    rcl_wykaz: str  # "number in the wykaz prac legislacyjnych RM"
    rcl_published: str
    rcl_no_stage: str
    rcl_metadata_note: str  # the text could not be read (bad file): metadata only
    consultation_letter: str
    consultation_days_from_letter: str
    consultation_deadline_in_letter: str  # deadline could not be read: "see the letter"
    consultation_email: str
    action_email_ministry: str
    action_in_polish: str
    action_rcl_comment: str
    rcl_results_hint: str
    rcl_sent_to_sejm: str
    link_rcl_project: str
    link_bill_text: str  # "Bill text" (the format follows in brackets)
    link_justification: str
    link_osr: str
    link_wykaz: str
    link_ministry_plan: str  # a ministry's own register, which RCL sometimes links instead
    tag_rcl: str
    # Bills the government has only announced (wykaz prac legislacyjnych RM)
    wykaz_header: str
    wykaz_intention: str  # "no text yet", above everything the model wrote
    wykaz_stage: str
    wykaz_published: str
    wykaz_planned: str  # the quarter the Council of Ministers means to adopt it in
    wykaz_metadata_note: str
    wykaz_organ_unknown: str
    action_wykaz_interest: str  # art. 7 of the lobbying act: anyone may file a zgłoszenie
    action_rcl_interest: str  # the same, in short: the RCL card already offers other moves
    link_wykaz_entry: str
    tag_wykaz: str
    # Status updates named after their event (see `models.update_event`)
    rcl_process_closed: str  # the project was closed on RCL without reaching the Sejm
    wykaz_process_closed: str  # the government took the project off its plan
    committee_report: str
    subcommittee_report: str
    proposes: str
    deadline_until: str  # "deadline" (a computed statutory deadline follows)
    deadline_passed: str  # the same deadline, once it has run out
    # Said instead of the usual duration once the step has outlived it (`models.stalled_days`).
    stalled_for_weeks: str
    stalled_for_months: str
    urgent_mode: str  # marks "what comes next" for a bill declared pilny (art. 123)
    # Public hearings: the reminder before applications close
    hearing_deadline_header: str
    hearing_on: str
    hearing_apply_until: str
    hearing_applications_closed: str
    hearing_hint: str
    tag_hearing: str
    # Amendments summarised from their document (Senate resolution, committee "-A" report)
    amendments_senate: str
    amendments_committee: str
    link_amendments: str
    update_headers: dict[str, str] = field(default_factory=dict)  # by event key
    # Fragments (lower case) of a `SejmReading.decision` and a `CommitteeReport.proposal`;
    # first match wins, an unknown value passes through in Polish.
    decision_labels: dict[str, str] = field(default_factory=dict)
    proposal_labels: dict[str, str] = field(default_factory=dict)
    event_tags: dict[str, str] = field(default_factory=dict)  # voting, senate, president, ...
    next_step_labels: dict[str, str] = field(default_factory=dict)  # by phase key, see models
    # By phase key: how long the step usually takes, shown when no sitting is scheduled yet.
    typical_durations: dict[str, str] = field(default_factory=dict)
    # The same two, for a bill declared pilny (art. 123): the constitutional terms are shorter
    # and the Sejm moves in days. Consulted first for such a bill, the normal entry fills the
    # phases the urgent mode does not change.
    urgent_step_labels: dict[str, str] = field(default_factory=dict)
    urgent_durations: dict[str, str] = field(default_factory=dict)
    # By phase key: what to say when the public has no move right now (and what comes next).
    no_action_labels: dict[str, str] = field(default_factory=dict)
    # The steps of the "path" line, in order: rcl, sejm, committee, readings, senate, president,
    # journal, in_force.
    path_steps: dict[str, str] = field(default_factory=dict)
    # The card's "stage" line: by Sejm stage type (`Stage.stage_type`) ...
    stage_labels: dict[str, str] = field(default_factory=dict)
    # ... and by a fragment of an RCL stage name (lower case), first match wins.
    rcl_stage_labels: dict[str, str] = field(default_factory=dict)
    date_format: str = "%Y-%m-%d"
    stage_type_labels: dict[str, str] = field(default_factory=dict)
    senate_position_labels: dict[str, str] = field(default_factory=dict)
    category_labels: dict[Category, str] = field(default_factory=dict)
    category_tags: dict[Category, str] = field(default_factory=dict)
    applicant_labels: dict[ApplicantType, str] = field(default_factory=dict)


RU = Labels(
    new_bill_header="Новый законопроект",
    finished_bill_header="Законопроект: процесс завершён",
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
    print_number="druk nr",
    partial_text_note="Анализ основан на неполном тексте документа.",
    new_stages="Новые стадии",
    reading_stage="{numeral} чтение на заседании Сейма",
    process_closed="Сейм отклонил проект, процесс завершён.",
    process_not_enacted="Процесс завершён: закон не принят.",
    process_veto_sustained=(
        "Сейм не отклонил вето Президента (нужно 3/5 голосов): закон не принят."
    ),
    stage_veto_sustained="закон не принят повторно после вето Президента",
    process_passed="Сейм принял закон.",
    link_process="Ход процесса в Сейме",
    link_pdf="PDF",
    link_rcl="RCL",
    link_text_after3="Текст после III чтения",
    joint_prints="Рассматривается совместно с druk",
    joint_bill_header="Альтернативный проект того же закона",
    joint_bill_note=(
        "Рассматривается совместно с druk {numbers}: комиссия рассматривает их вместе, "
        "дальнейший ход дела — в этой ветке."
    ),
    current_summary="Суть проекта",
    changes_since_previous="Что изменилось с прошлого раза",
    reanalyzed_note="Текст проекта обновился, анализ выполнен заново.",
    run_report_title="lexinform run report",
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
    in_force_header_dated="Закон вступил в силу",
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
    tag_consultation_results="мнениявконсультациях",
    tag_term="kadencja",
    tag_ukraine="Украина",
    consultation_link="страница проекта на сайте Сейма",
    consultation_page="страница консультаций на сайте Сейма",
    action_now="Что можно сделать сейчас",
    action_senate="направить мнение в профильную комиссию Сената",
    path="Путь",
    action_send_opinion="направить мнение через",
    action_consultation_page="страницу проекта на сайте Сейма",
    action_committee="направить мнение в комиссию —",
    action_before_sitting="до заседания",
    action_hearing="подать заявку на участие в публичных слушаниях",
    next_step="Что дальше",
    agenda_committee_header="Заседание комиссии",
    agenda_sejm_header="В повестке заседания Сейма",
    agenda_item="Пункт повестки",
    sitting_moved_from="Заседание перенесено с",
    sejm_sitting="заседание Сейма №",
    link_video="Трансляция",
    link_committee="Страница комиссии",
    link_senate_bills="законы в Сенате",
    tag_committee_sitting="заседаниекомиссии",
    tag_sejm_sitting="заседаниесейма",
    consultation_results_header="Опубликованы мнения из консультаций",
    consultation_results_hint=(
        "мнения, поданные в ходе общественных консультаций, доступны на странице проекта"
    ),
    rcl_header="Правительственный проект (RCL)",
    rcl_wykaz="номер в wykazie prac RM",
    rcl_published="Опубликован на RCL",
    rcl_no_stage="проект опубликован на RCL, работа над ним ещё не началась",
    rcl_metadata_note=(
        "Текст проекта на RCL не удалось прочитать — анализ по названию и описанию."
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
    link_ministry_plan="План работ министерства",
    tag_rcl="RCL",
    wykaz_header="План правительства",
    wykaz_intention=(
        "Это пока только намерение: текста проекта ещё нет. Запись в плане работ"
        " правительства от {date}. Разбор текста придёт отдельной карточкой, когда проект"
        " опубликуют на RCL и начнутся публичные консультации."
    ),
    wykaz_stage="проект внесён в план работ правительства, текста ещё нет",
    wykaz_published="Внесён в план работ",
    wykaz_planned="принятие правительством: {quarter} кв. {year}",
    wykaz_metadata_note=(
        "Оценка по описанию из плана работ правительства: текста проекта ещё не существует."
    ),
    wykaz_organ_unknown="профильное министерство",
    action_wykaz_interest=(
        "подать в {organ} zgłoszenie zainteresowania pracami nad projektem — заявить, что вы"
        " следите за проектом. Это может любой: гражданство и юридическое лицо не нужны, частное"
        " лицо подаёт от своего имени (ст. 7 ustawy o działalności lobbingowej). Зачем: в"
        " заявлении вы пишете, какой интерес защищаете и какого решения добиваетесь, — и оно"
        " попадает в BIP к документам проекта ещё до того, как текст написан, то есть когда его"
        " проще всего изменить; кроме того, подавший вправе участвовать в публичном слушании"
        " проекта в Сейме, если оно будет назначено"
    ),
    action_rcl_interest=(
        "подать в {organ} zgłoszenie zainteresowania pracami nad projektem — это может любой,"
        " и подавший вправе участвовать в публичном слушании в Сейме (ст. 7 и 8 ust. 2"
        " ustawy o działalności lobbingowej)"
    ),
    link_wykaz_entry="Запись в плане работ",
    tag_wykaz="wykazRM",
    rcl_process_closed="Проект закрыт на RCL, в Сейм не направлен.",
    wykaz_process_closed="Проект снят с плана работ правительства.",
    committee_report="отчёт комиссии (sprawozdanie)",
    subcommittee_report="отчёт подкомиссии",
    proposes="предлагает",
    deadline_until="срок до",
    deadline_passed="срок истёк",
    stalled_for_weeks="без движения уже {weeks} нед.",
    stalled_for_months="без движения уже {months} мес.",
    urgent_mode="срочный режим, tryb pilny",
    hearing_deadline_header="Заявки на публичные слушания",
    hearing_on="слушания",
    hearing_apply_until="заявки на участие до",
    hearing_applications_closed="приём заявок на участие закрыт",
    hearing_hint=(
        "заявку подаёт любой желающий через систему Сейма (формуляр на странице комиссии);"
        " каждый заявитель получает слово"
    ),
    tag_hearing="слушания",
    amendments_senate="Что меняют поправки Сената",
    amendments_committee="Что меняют поправки (по отчёту комиссии)",
    link_amendments="Текст поправок (PDF)",
    update_headers={
        "update": "Обновление",
        "print_assigned": "Присвоен номер druku",
        "start": "Проект поступил в Сейм",
        "first_reading_referral": "Направлен на I чтение",
        "referral": "Направлен в комиссию",
        "referrals": "Направлен в комиссии",
        "referral_plenary": "I чтение пройдёт на заседании Сейма",
        "first_reading": "I чтение прошло",
        "committee_work": "Работа в комиссии началась",
        "committee_report": "Отчёт комиссии",
        "subcommittee_report": "Отчёт подкомиссии",
        "committee_rejects": "Комиссия предлагает отклонить проект",
        "hearing": "Назначены публичные слушания",
        "second_reading": "II чтение прошло",
        "second_reading_amendments": "Поправки во II чтении: проект вернулся в комиссию",
        "third_reading": "III чтение",
        "passed": "Сейм принял закон",
        "rejected": "Сейм отклонил проект",
        "withdrawn_by_applicant": "Проект отозван",
        "veto_sustained": "Вето Президента осталось в силе",
        "not_enacted": "Процесс завершён: закон не принят",
        "senate": "Позиция Сената",
        "senate_no_amendments": "Сенат принял закон без поправок",
        "senate_amendments": "Сенат внёс поправки",
        "senate_rejected": "Сенат отклонил закон",
        "senate_considered": "Сейм рассмотрел поправки Сената",
        "to_president": "Закон передан Президенту",
        "signed": "Президент подписал закон",
        "veto": "Президент наложил вето",
        "tribunal": "Закон направлен в Конституционный трибунал",
        "text_changed": "Новая версия текста",
        "withdrawn": "Проект отозван",
        "discontinued": "Проект прекращён с концом каденции",
        "rcl_stage": "Новая стадия на RCL",
        "rcl_to_sejm": "Проект направлен в Сейм",
        "rcl_closed": "Проект закрыт на RCL",
        "rcl_started": "Проект опубликован на RCL",
        "wykaz_withdrawn": "Правительство отказалось от проекта",
        "wykaz_adopted": "Правительство приняло проект",
    },
    decision_labels={
        "niezwłocznie przystąpiono do iii": "сразу перешли к III чтению",
        "skierowano ponownie do komisji": "возвращён в комиссию для рассмотрения поправок",
        "skierowano do komisji": "направлен в комиссию",
        "przyjęto część poprawek": "часть поправок Сената принята",
        "przyjęto poprawki": "поправки Сената приняты",
        "odrzucono poprawki": "поправки Сената отклонены",
        "uchwalono": "закон принят",
        "odrzucono": "проект отклонён",
    },
    proposal_labels={
        "załączony projekt": "принять проект в новой редакции (текст приложен)",
        "przyjąć bez poprawek": "принять без поправок",
        "przyjąć część poprawek": "принять часть поправок",
        "odrzucić poprawki": "отклонить поправки",
        "przyjąć poprawki": "принять поправки",
        "odrzucić": "отклонить проект",
    },
    event_tags={
        "voting": "голосование",
        "senate": "сенат",
        "president": "президент",
        "veto": "вето",
        "amendments": "поправки",
        "new_text": "новыйтекст",
        "committee": "комиссия",
        "hearing": "слушания",
        "passed": "принят",
        "rejected": "непринят",
        "tribunal": "трибунал",
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
        "first_reading_committee_unnamed": "I чтение в комиссии",
        "first_reading_sitting": "I чтение на заседании Сейма",
        "committee_work": (
            "работа в комиссии — {committee} (sprawozdanie), затем II чтение на заседании Сейма"
        ),
        "committee_work_unnamed": "работа в комиссии (sprawozdanie), затем II чтение в Сейме",
        "second_reading": "II чтение на заседании Сейма",
        "second_reading_committee": (
            "комиссия рассматривает поправки II чтения — {committee} (дополнительное"
            " sprawozdanie), затем III чтение"
        ),
        "second_reading_committee_unnamed": (
            "комиссия рассматривает поправки II чтения, затем III чтение"
        ),
        "third_reading": "III чтение и голосование в Сейме",
        "senate": "рассмотрение в Сенате (до 30 дней)",
        "senate_amendments": "Сейм рассматривает поправки Сената",
        "senate_rejection": (
            "Сейм голосует по решению Сената отклонить закон: закон будет принят, только если"
            " Сейм отклонит это решение абсолютным большинством"
        ),
        "president": "подпись Президента (до 21 дня), затем публикация в Dziennik Ustaw",
        "publication": "публикация в Dziennik Ustaw",
        "in_force": "вступление в силу {date}",
        "in_force_unknown": "вступление в силу (дата пока не указана)",
        "veto": "Сейм может отклонить вето (3/5 голосов)",
        "tribunal": "решение Конституционного трибунала",
        "wykaz": (
            "публикация проекта на RCL и публичные консультации, затем комитеты Совета министров,"
            " Rada Ministrów и направление в Сейм"
        ),
        "wykaz_to_rcl": "публикация проекта на RCL и публичные консультации",
        "wykaz_adopted": "внесение проекта в Сейм и присвоение номера druku",
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
    typical_durations={
        "pre_print": "обычно от нескольких дней до нескольких месяцев",
        "first_reading": "обычно 2–6 недель после поступления",
        "first_reading_committee": "обычно 2–6 недель после поступления",
        "first_reading_sitting": "обычно 2–6 недель после поступления",
        "committee_work": "от нескольких недель до года",
        "second_reading": "часто на одном заседании с III чтением",
        "second_reading_committee": "обычно дни — до ближайшего блока голосований",
        "third_reading": "часто на одном заседании со II чтением",
        "senate_amendments": "обычно на ближайшем заседании Сейма",
        "senate_rejection": "обычно на ближайшем заседании Сейма",
        "veto": "Сейм голосует, когда решит: от недель до конца каденции",
        "tribunal": "трибунал рассматривает месяцами",
        "publication": "обычно 1–4 недели после подписи",
        "wykaz": "обычно 1–6 месяцев до публикации проекта",
        "wykaz_to_rcl": "обычно несколько недель",
        "wykaz_adopted": "обычно несколько недель",
        "rcl_committees": "обычно 1–3 месяца",
        "rcl_council": "обычно несколько недель",
        "rcl_to_sejm": "обычно несколько дней",
    },
    urgent_step_labels={
        "senate": "рассмотрение в Сенате (срочный режим: до 14 дней)",
        "president": (
            "подпись Президента (срочный режим: до 7 дней), затем публикация в Dziennik Ustaw"
        ),
    },
    urgent_durations={
        "first_reading": "обычно несколько дней после поступления",
        "first_reading_committee": "обычно несколько дней после поступления",
        "first_reading_sitting": "обычно несколько дней после поступления",
        "committee_work": "обычно дни, а не недели",
        "second_reading": "III чтение обычно сразу после II",
        "third_reading": "обычно на том же заседании, что и II чтение",
        "senate_amendments": "обычно на ближайшем заседании Сейма",
        "publication": "обычно несколько дней после подписи",
    },
    no_action_labels={
        "pre_print": "пока ничего — следующая возможность: замечания в комиссию после I чтения",
        "pre_print_consultation": (
            "пока ничего — следующая возможность: замечания в комиссию после I чтения"
        ),
        "first_reading": "пока ничего — следующая возможность: замечания в комиссию после I чтения",
        "first_reading_committee": (
            "пока ничего — следующая возможность: замечания в комиссию после I чтения"
        ),
        "first_reading_sitting": (
            "пока ничего — следующая возможность: замечания в комиссию после I чтения"
        ),
        "committee_work": (
            "написать в комиссию, которая ведёт проект — её название указано на странице"
            " процесса в Сейме"
        ),
        "second_reading": (
            "пока ничего — после голосования в Сейме мнение можно направить в комиссию Сената"
        ),
        "second_reading_committee": (
            "написать в комиссию, которая дорабатывает поправки — её название указано на"
            " странице процесса в Сейме"
        ),
        "third_reading": (
            "пока ничего — после голосования в Сейме мнение можно направить в комиссию Сената"
        ),
        "senate_amendments": "пока ничего — Сейм решает по поправкам Сената",
        "senate_rejection": "пока ничего — Сейм решает, отклонить ли решение Сената",
        "president": "пока ничего — закон у Президента",
        "publication": "пока ничего — ждём публикации в Dziennik Ustaw",
        "in_force": "пока ничего — закон принят, остаётся подготовиться к вступлению в силу",
        "in_force_unknown": "пока ничего — закон принят, дата вступления в силу ещё не известна",
        "veto": "пока ничего — решение за Сеймом",
        "tribunal": "пока ничего — решение за Конституционным трибуналом",
        "wykaz_to_rcl": (
            "пока ничего — ждём публикации проекта на RCL, тогда откроются консультации"
        ),
        "wykaz_adopted": (
            "пока ничего — правительство приняло проект, ждём внесения в Сейм и номера druku"
        ),
        "rcl_to_sejm": "пока ничего — ждём номер druku, затем I чтение и комиссия",
    },
    path_steps={
        "wykaz": "план",
        "rcl": "RCL",
        "sejm": "Сейм",
        "committee": "комиссии",
        "readings": "II и III чтение",
        "senate": "Сенат",
        "president": "Президент",
        "journal": "Dz.U.",
        "in_force": "в силе",
    },
    stage_labels={
        "Start": "проект поступил в Сейм",
        "ReadingReferral": "направлен на I чтение",
        "Referral": "направлен в комиссию",
        "Reading": "I чтение в комиссиях",
        "CommitteeWork": "работа в комиссии",
        "CommitteeReport": "отчёт комиссии (sprawozdanie)",
        "Voting": "голосование в Сейме",
        "SenatePosition": "позиция Сената",
        "End": "процесс в Сейме завершён",
    },
    rcl_stage_labels={
        "zgłoszenia lobbingowe": "лоббистские заявления",
        "uzgodnienia": "межведомственные согласования",
        "konsultacje publiczne": "общественные консультации",
        "opiniowanie": "сбор мнений министерств и партнёров",
        "komisja prawnicza": "Юридическая комиссия правительства",
        "stały komitet": "Постоянный комитет Совета министров",
        "komitet": "комитет Совета министров",
        "notyfikacja": "нотификация в Европейской комиссии",
        "rada ministrów": "Совет министров",
        "do sejmu": "направлен в Сейм",
    },
    stage_type_labels={
        "ToPresident": "Закон передан Президенту",
        "PresidentSignature": "✍️ Президент подписал закон",
        "Veto": "⛔ Президент наложил вето",
        "PresidentToTribunal": "⚖️ Президент направил закон в Конституционный трибунал",
        "PublicHearing": "📢 Публичные слушания (wysłuchanie publiczne)",
        "SenatePositionConsideration": "Сейм рассмотрел позицию Сената",
        "GovermentPosition": "поступила позиция правительства",
        "Opinion": "поступило мнение организации",
    },
    senate_position_labels={
        "nie wniósł poprawek": "Сенат принял закон без поправок",
        "wniósł poprawki": "Сенат внёс поправки",
        "wniósł poprawkę": "Сенат внёс поправку",
        "odrzucił ustawę": "Сенат отклонил закон",
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
    finished_bill_header="Bill: the process is over",
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
    print_number="print no.",
    partial_text_note="The analysis is based on a partial text of the document.",
    new_stages="New stages",
    reading_stage="{numeral} reading at a Sejm sitting",
    process_closed="The Sejm rejected the bill; the process is over.",
    process_not_enacted="The process is over: no law was enacted.",
    process_veto_sustained=(
        "The Sejm did not override the veto (a 3/5 majority is needed): no law was enacted."
    ),
    stage_veto_sustained="not passed again after the President's veto",
    process_passed="The Sejm passed the bill.",
    link_process="Legislative process",
    link_pdf="Print PDF",
    link_rcl="RCL",
    link_text_after3="Text after 3rd reading",
    joint_prints="Considered jointly with print",
    joint_bill_header="Alternative bill on the same subject",
    joint_bill_note=(
        "Considered jointly with print {numbers}: the committee works on them together; "
        "what happens next is posted in this thread."
    ),
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
    in_force_header_dated="The act has entered into force",
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
    tag_consultation_results="consultationopinions",
    tag_term="term",
    tag_ukraine="Ukraine",
    consultation_link="the bill's page on the Sejm website",
    consultation_page="consultation page on the Sejm website",
    action_now="What you can do now",
    action_senate="send an opinion to the competent Senate committee",
    path="Path",
    action_send_opinion="send an opinion via",
    action_consultation_page="the bill's page on the Sejm website",
    action_committee="send an opinion to the committee —",
    action_before_sitting="before the sitting on",
    action_hearing="apply to take part in the public hearing",
    next_step="What comes next",
    agenda_committee_header="Committee sitting",
    agenda_sejm_header="On the agenda of a Sejm sitting",
    agenda_item="Agenda item",
    sitting_moved_from="The sitting was moved from",
    sejm_sitting="Sejm sitting no.",
    link_video="Live stream",
    link_committee="Committee page",
    link_senate_bills="bills in the Senate",
    tag_committee_sitting="committeesitting",
    tag_sejm_sitting="sejmsitting",
    consultation_results_header="Consultation opinions published",
    consultation_results_hint=(
        "the opinions submitted during the public consultation are available on the bill's page"
    ),
    rcl_header="Government bill (RCL)",
    rcl_wykaz="wykaz prac RM number",
    rcl_published="Published on RCL",
    rcl_no_stage="published on RCL, work has not started yet",
    rcl_metadata_note=("The text on RCL could not be read — analysed from title and description."),
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
    link_ministry_plan="Ministry work plan",
    tag_rcl="RCL",
    wykaz_header="Government plan",
    wykaz_intention=(
        "An intention so far: there is no draft text yet. Entered in the government's"
        " legislative plan on {date}. The text will be analysed in a separate card once the"
        " project is published on RCL and the public consultation starts."
    ),
    wykaz_stage="entered in the government's legislative plan, no text yet",
    wykaz_published="Entered in the plan",
    wykaz_planned="adoption by the government: Q{quarter} {year}",
    wykaz_metadata_note=("Scored from the register entry: the draft text does not exist yet."),
    wykaz_organ_unknown="the responsible ministry",
    action_wykaz_interest=(
        "file a zgłoszenie zainteresowania pracami nad projektem with {organ} — a declaration"
        " that you are following the project. Anyone may: no citizenship and no legal entity"
        " needed, a private individual files on their own behalf (art. 7 of the lobbying act)."
        " What it is for: you state the interest you want to protect and the solution you will"
        " seek, and it goes into the BIP file of the project before the text is written, when it"
        " is easiest to change; it also entitles you to take part in the Sejm's public hearing"
        " of the bill, should one be held"
    ),
    action_rcl_interest=(
        "file a zgłoszenie zainteresowania pracami nad projektem with {organ} — anyone may,"
        " and whoever does may take part in the public hearing in the Sejm (art. 7 and"
        " 8 ust. 2 of the lobbying act)"
    ),
    link_wykaz_entry="Register entry",
    tag_wykaz="wykazRM",
    rcl_process_closed="The project was closed on RCL without reaching the Sejm.",
    wykaz_process_closed="The project was taken off the government's plan.",
    committee_report="committee report (sprawozdanie)",
    subcommittee_report="sub-committee report",
    proposes="proposes to",
    deadline_until="deadline",
    deadline_passed="deadline passed",
    stalled_for_weeks="no movement for {weeks} weeks",
    stalled_for_months="no movement for {months} months",
    urgent_mode="urgent procedure, tryb pilny",
    hearing_deadline_header="Public hearing: applications close",
    hearing_on="hearing on",
    hearing_apply_until="applications until",
    hearing_applications_closed="applications are closed",
    hearing_hint=(
        "anyone may apply through the Sejm's system (form on the committee page);"
        " every applicant gets to speak"
    ),
    tag_hearing="hearing",
    amendments_senate="What the Senate's amendments change",
    amendments_committee="What the amendments change (per the committee's report)",
    link_amendments="Amendments (PDF)",
    update_headers={
        "update": "Update",
        "print_assigned": "Print number assigned",
        "start": "Bill received by the Sejm",
        "first_reading_referral": "Referred to the first reading",
        "referral": "Referred to a committee",
        "referrals": "Referred to committees",
        "referral_plenary": "First reading at a Sejm sitting",
        "first_reading": "First reading held",
        "committee_work": "Committee work started",
        "committee_report": "Committee report",
        "subcommittee_report": "Sub-committee report",
        "committee_rejects": "The committee proposes to reject the bill",
        "hearing": "Public hearing announced",
        "second_reading": "Second reading held",
        "second_reading_amendments": "Amendments at the 2nd reading: back to the committee",
        "third_reading": "Third reading",
        "passed": "The Sejm passed the bill",
        "rejected": "The Sejm rejected the bill",
        "withdrawn_by_applicant": "The bill was withdrawn",
        "veto_sustained": "The President's veto stood",
        "not_enacted": "The process is over: no law was enacted",
        "senate": "Senate position",
        "senate_no_amendments": "The Senate passed the act without amendments",
        "senate_amendments": "The Senate introduced amendments",
        "senate_rejected": "The Senate rejected the act",
        "senate_considered": "The Sejm considered the Senate's amendments",
        "to_president": "Sent to the President",
        "signed": "The President signed the act",
        "veto": "The President vetoed the act",
        "tribunal": "Referred to the Constitutional Tribunal",
        "text_changed": "New version of the text",
        "withdrawn": "Bill withdrawn",
        "discontinued": "Bill lapsed with the end of the term",
        "rcl_stage": "New stage on RCL",
        "rcl_to_sejm": "Sent to the Sejm",
        "rcl_closed": "Project closed on RCL",
        "rcl_started": "Draft published on RCL",
        "wykaz_withdrawn": "The government dropped the project",
        "wykaz_adopted": "The government adopted the project",
    },
    decision_labels={
        "niezwłocznie przystąpiono do iii": "moved straight on to the 3rd reading",
        "skierowano ponownie do komisji": "sent back to the committee to consider amendments",
        "skierowano do komisji": "referred to a committee",
        "przyjęto część poprawek": "some of the Senate's amendments accepted",
        "przyjęto poprawki": "the Senate's amendments accepted",
        "odrzucono poprawki": "the Senate's amendments rejected",
        "uchwalono": "passed",
        "odrzucono": "rejected",
    },
    proposal_labels={
        "załączony projekt": "adopt the bill as amended (text attached)",
        "przyjąć bez poprawek": "adopt without amendments",
        "przyjąć część poprawek": "accept some of the amendments",
        "odrzucić poprawki": "reject the amendments",
        "przyjąć poprawki": "accept the amendments",
        "odrzucić": "reject the bill",
    },
    event_tags={
        "voting": "vote",
        "senate": "senate",
        "president": "president",
        "veto": "veto",
        "amendments": "amendments",
        "new_text": "newtext",
        "committee": "committee",
        "hearing": "hearing",
        "passed": "passed",
        "rejected": "notenacted",
        "tribunal": "tribunal",
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
        "first_reading_committee_unnamed": "first reading in committee",
        "first_reading_sitting": "first reading at a Sejm sitting",
        "committee_work": (
            "committee work — {committee} (report), then the second reading at a Sejm sitting"
        ),
        "committee_work_unnamed": "committee work (report), then the second reading in the Sejm",
        "second_reading": "second reading at a Sejm sitting",
        "second_reading_committee": (
            "the committee works on the second reading's amendments — {committee} (additional"
            " report), then the third reading"
        ),
        "second_reading_committee_unnamed": (
            "the committee works on the second reading's amendments, then the third reading"
        ),
        "third_reading": "third reading and the vote in the Sejm",
        "senate": "consideration by the Senate (up to 30 days)",
        "senate_amendments": "the Sejm considers the Senate's amendments",
        "senate_rejection": (
            "the Sejm votes on the Senate's rejection: the law passes only if the Sejm throws"
            " that rejection out by an absolute majority"
        ),
        "president": (
            "the President's signature (up to 21 days), then publication in Dziennik Ustaw"
        ),
        "publication": "publication in Dziennik Ustaw",
        "in_force": "entry into force on {date}",
        "in_force_unknown": "entry into force (date not stated yet)",
        "veto": "the Sejm may override the veto (3/5 majority)",
        "tribunal": "ruling of the Constitutional Tribunal",
        "wykaz": (
            "the draft published on RCL with a public consultation, then the committees of the"
            " Council of Ministers, the Council itself and the Sejm"
        ),
        "wykaz_to_rcl": "the draft published on RCL with a public consultation",
        "wykaz_adopted": "submission to the Sejm and a print number",
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
    typical_durations={
        "pre_print": "usually days to months",
        "first_reading": "usually 2–6 weeks after submission",
        "first_reading_committee": "usually 2–6 weeks after submission",
        "first_reading_sitting": "usually 2–6 weeks after submission",
        "committee_work": "weeks to a year",
        "second_reading": "often at the same sitting as the third reading",
        "second_reading_committee": "usually days — until the next block of votes",
        "third_reading": "often at the same sitting as the second reading",
        "senate_amendments": "usually at the next Sejm sitting",
        "senate_rejection": "usually at the next Sejm sitting",
        "veto": "the Sejm votes when it chooses: weeks to the end of the term",
        "tribunal": "the Tribunal takes months",
        "publication": "usually 1–4 weeks after the signature",
        "wykaz": "usually 1–6 months until the draft is published",
        "wykaz_to_rcl": "usually a few weeks",
        "wykaz_adopted": "usually a few weeks",
        "rcl_committees": "usually 1–3 months",
        "rcl_council": "usually a few weeks",
        "rcl_to_sejm": "usually a few days",
    },
    urgent_step_labels={
        "senate": "consideration by the Senate (urgent procedure: up to 14 days)",
        "president": (
            "the President's signature (urgent procedure: up to 7 days), then publication in"
            " Dziennik Ustaw"
        ),
    },
    urgent_durations={
        "first_reading": "usually a few days after submission",
        "first_reading_committee": "usually a few days after submission",
        "first_reading_sitting": "usually a few days after submission",
        "committee_work": "days rather than weeks",
        "second_reading": "the third reading usually follows at once",
        "third_reading": "usually at the same sitting as the second reading",
        "senate_amendments": "usually at the next Sejm sitting",
        "publication": "usually a few days after the signature",
    },
    no_action_labels={
        "pre_print": "nothing yet — next chance: comments to the committee after the first reading",
        "pre_print_consultation": (
            "nothing yet — next chance: comments to the committee after the first reading"
        ),
        "first_reading": (
            "nothing yet — next chance: comments to the committee after the first reading"
        ),
        "first_reading_committee": (
            "nothing yet — next chance: comments to the committee after the first reading"
        ),
        "first_reading_sitting": (
            "nothing yet — next chance: comments to the committee after the first reading"
        ),
        "committee_work": (
            "write to the committee handling the bill — its name is on the Sejm process page"
        ),
        "second_reading": (
            "nothing yet — after the Sejm vote an opinion can go to the Senate committee"
        ),
        "second_reading_committee": (
            "write to the committee working on the amendments — its name is on the Sejm process"
            " page"
        ),
        "third_reading": (
            "nothing yet — after the Sejm vote an opinion can go to the Senate committee"
        ),
        "senate_amendments": "nothing yet — the Sejm decides on the Senate's amendments",
        "senate_rejection": "nothing yet — the Sejm decides whether to throw out the rejection",
        "president": "nothing yet — the act is with the President",
        "publication": "nothing yet — waiting for publication in Dziennik Ustaw",
        "in_force": "nothing yet — the act is passed, prepare for its entry into force",
        "in_force_unknown": "nothing yet — the act is passed, the entry-into-force date is unknown",
        "veto": "nothing yet — the Sejm decides",
        "tribunal": "nothing yet — the Constitutional Tribunal decides",
        "wykaz_to_rcl": (
            "nothing yet — waiting for the draft on RCL, which opens the consultation"
        ),
        "wykaz_adopted": (
            "nothing yet — the government adopted the draft; waiting for the Sejm and a print"
            " number"
        ),
        "rcl_to_sejm": (
            "nothing yet — waiting for the print number, then first reading and committee"
        ),
    },
    path_steps={
        "wykaz": "plan",
        "rcl": "RCL",
        "sejm": "Sejm",
        "committee": "committees",
        "readings": "2nd and 3rd reading",
        "senate": "Senate",
        "president": "President",
        "journal": "Dz.U.",
        "in_force": "in force",
    },
    stage_labels={
        "Start": "submitted to the Sejm",
        "ReadingReferral": "referred to the first reading",
        "Referral": "referred to committee",
        "Reading": "first reading in committee",
        "CommitteeWork": "committee work",
        "CommitteeReport": "committee report (sprawozdanie)",
        "Voting": "vote in the Sejm",
        "SenatePosition": "Senate position",
        "End": "Sejm process closed",
    },
    rcl_stage_labels={
        "zgłoszenia lobbingowe": "lobbying declarations",
        "uzgodnienia": "inter-ministerial agreement",
        "konsultacje publiczne": "public consultation",
        "opiniowanie": "opinions of ministries and partners",
        "komisja prawnicza": "the government's Legal Commission",
        "stały komitet": "Standing Committee of the Council of Ministers",
        "komitet": "committee of the Council of Ministers",
        "notyfikacja": "notification to the European Commission",
        "rada ministrów": "Council of Ministers",
        "do sejmu": "sent to the Sejm",
    },
    stage_type_labels={
        "ToPresident": "Sent to the President",
        "PresidentSignature": "✍️ Signed by the President",
        "Veto": "⛔ Vetoed by the President",
        "PresidentToTribunal": "⚖️ Referred by the President to the Constitutional Tribunal",
        "PublicHearing": "📢 Public hearing (wysłuchanie publiczne)",
        "SenatePositionConsideration": "Sejm considered the Senate position",
        "GovermentPosition": "the government's position arrived",
        "Opinion": "an organisation's opinion arrived",
    },
    senate_position_labels={
        "nie wniósł poprawek": "Senate passed the bill without amendments",
        "wniósł poprawki": "Senate introduced amendments",
        "wniósł poprawkę": "Senate introduced an amendment",
        "odrzucił ustawę": "Senate rejected the bill",
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
