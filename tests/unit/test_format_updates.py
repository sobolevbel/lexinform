"""The status update under a card: which stages it lists, what its header names, and how a stage
is rendered — the vote with its clubs, the Senate's position, the President's answer."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import ClubVotes, ProcessDetail, Stage, flatten_stages
from tests.fakes import make_analysis
from tests.formatting import ACT, assert_telegram_html, bill_of, change_of


def test_status_update_without_an_analysis_keeps_the_stages_before_the_closure(
    process_1962: ProcessDetail,
) -> None:
    bill = bill_of(process_1962).model_copy(update={"analysis": None})  # no importance badge
    change = change_of(
        "1962", flatten_stages(process_1962.stages)[-3:], closure_detected=True, passed=True
    )

    text = MessageFormatter("ru").status_update(bill, change).text

    assert_telegram_html(text)
    assert text.index("• 03.09.2026: отчёт комиссии") < text.index("Сейм принял закон.")


def test_status_update_lists_new_stages_and_the_closure(process_1962: ProcessDetail) -> None:
    bill = bill_of(process_1962, make_analysis(score=3))
    change = change_of(
        "1962", flatten_stages(process_1962.stages)[-3:], closure_detected=True, passed=True
    )

    text = MessageFormatter("ru").status_update(bill, change).text

    assert_telegram_html(text)
    # The header names the newest event; the stages are bullets in the reader's language.
    assert "🏛 <b>Сейм рассмотрел поправки Сената — druk nr 1962</b>" in text
    assert (
        "• 03.09.2026: отчёт комиссии (sprawozdanie) (druk nr 3014): предлагает принять часть"
        in text
    )
    assert "• 04.09.2026: Сейм рассмотрел позицию Сената — часть поправок Сената принята" in text
    assert "• процесс в Сейме завершён" in text and "Uchwalono" not in text
    assert "Сейм принял закон." in text
    assert "#kadencja10druk1962" in text


def test_update_header_names_the_event_and_the_closure_line_is_not_repeated(
    process_1962: ProcessDetail,
) -> None:
    flat = flatten_stages(process_1962.stages)
    third = next(st for st in flat if st.stage_type == "SejmReading" and "III" in st.stage_name)
    voting = next(st for st in flat if st.stage_type == "Voting")
    referral = next(st for st in flat if st.stage_type == "Referral")
    report = next(st for st in flat if st.stage_type == "CommitteeReport")
    rejecting = report.model_copy(update={"proposal": "odrzucić projekt ustawy"})
    bill = bill_of(process_1962)
    fmt = MessageFormatter("ru")

    passed = fmt.status_update(
        bill, change_of("1962", [third, voting], closure_detected=True, passed=True)
    ).text
    rejected = fmt.status_update(
        bill, change_of("1962", [], closure_detected=True, passed=False)
    ).text
    referred = fmt.status_update(bill, change_of("1962", [referral])).text
    reported = fmt.status_update(bill, change_of("1962", [rejecting])).text

    assert passed.startswith("✅ <b>Сейм принял закон — druk nr 1962</b>")
    assert "• 17.07.2026: III чтение на заседании Сейма — закон принят" in passed
    assert "Сейм принял закон." not in passed  # the header said it
    assert rejected.startswith("🏁 <b>Процесс завершён: закон не принят — druk nr 1962</b>")
    assert referred.startswith("📮 <b>Направлен в комиссию — druk nr 1962</b>")
    assert reported.startswith("❌ <b>Комиссия предлагает отклонить проект — druk nr 1962</b>")
    assert "предлагает отклонить проект" in reported


def test_update_repeats_one_sentence_of_the_summary_unless_the_analysis_changed(
    process_1962: ProcessDetail,
) -> None:
    analysis = make_analysis()
    analysis.summary = "Первое предложение о сути. Второе предложение с деталями."
    referral = next(st for st in flatten_stages(process_1962.stages) if st.stage_type == "Referral")
    bill = bill_of(process_1962, analysis)
    fmt = MessageFormatter("ru")

    plain = fmt.status_update(bill, change_of("1962", [referral])).text
    reanalysed = fmt.status_update(bill, change_of("1962", [], content_changed=True)).text

    assert "📝 <b>Суть проекта:</b> Первое предложение о сути." in plain
    assert "Второе предложение" not in plain
    assert "📝 <b>Суть проекта</b>\nПервое предложение о сути. Второе предложение" in reanalysed


def test_frame_stages_are_dropped_when_their_children_are_listed(
    process_1962: ProcessDetail,
) -> None:
    referral_parent = process_1962.stages[1]  # "Skierowano do I czytania w komisjach" + child
    committee_work = process_1962.stages[3]  # "Praca w komisjach po I czytaniu" + report
    bill = bill_of(process_1962)
    fmt = MessageFormatter("ru")

    with_children = fmt.status_update(
        bill,
        change_of(
            "1962",
            [referral_parent, *referral_parent.children, committee_work, *committee_work.children],
        ),
    ).text
    alone = fmt.status_update(bill, change_of("1962", [referral_parent])).text

    assert "Skierowano" not in with_children and "Praca w komisjach" not in with_children
    assert "• 17.11.2025: 📮 Направлен в комиссию: SPC" in with_children
    assert "предлагает принять проект в новой редакции (текст приложен)" in with_children
    assert "• 17.11.2025: направлен на I чтение" in alone


def test_status_update_after_a_re_analysis_shows_the_diff(process_3039: ProcessDetail) -> None:
    analysis = make_analysis(score=4)
    analysis.changes_since_previous = ["Срок подачи сокращён", "Добавлена категория студентов"]
    change = change_of("3039", [], content_changed=True)

    text = MessageFormatter("ru").status_update(bill_of(process_3039, analysis), change).text

    assert_telegram_html(text)
    assert "🟠 ●●●●○ 4/5" in text
    assert "Суть проекта" in text
    assert "🆕 <b>Что изменилось с прошлого раза</b>" in text and "Срок подачи сокращён" in text
    assert "Новые стадии" not in text


def test_status_update_tags_name_the_events(process_1962: ProcessDetail) -> None:
    bill = bill_of(process_1962)
    flat = flatten_stages(process_1962.stages)
    voting = next(st for st in flat if st.stage_type == "Voting")
    senate = flat[0].model_copy(update={"stage_type": "SenatePosition"})
    referral = flat[0].model_copy(update={"stage_type": "Referral"})
    fmt = MessageFormatter("ru")

    eventful = fmt.status_update(bill, change_of("1962", [voting, senate], content_changed=True))
    plain = fmt.status_update(bill, change_of("1962", [referral]))
    withdrawn = fmt.status_update(
        bill, change_of("1962", [], withdrawn=True, closure_detected=True)
    )

    assert eventful.text.splitlines()[-1] == (
        "#голосование #сенат #новыйтекст #важность5 #легализация #kadencja10druk1962"
    )
    # A referral is a searchable event of its own: "all the bills now in committee".
    assert plain.text.splitlines()[-1] == ("#комиссия #важность5 #легализация #kadencja10druk1962")
    assert "#отозван #важность5 #легализация #kadencja10druk1962" in withdrawn.text


def test_replies_of_a_linked_print_carry_the_tag_of_the_card_they_continue(
    process_1962: ProcessDetail,
) -> None:
    fmt = MessageFormatter("ru")
    referral = flatten_stages(process_1962.stages)[0].model_copy(update={"stage_type": "Referral"})
    change = change_of("1962", [referral])
    from_rcl = bill_of(process_1962, linked_number="RCL/12414100", linked_wykaz_number="UC164")
    from_rcl_unnumbered = bill_of(process_1962, linked_number="RCL/12414100")
    from_rpw = bill_of(process_1962, linked_number="RPW/29075/2026")

    assert fmt.status_update(from_rcl, change).text.endswith("#kadencja10druk1962 #UC164")
    assert fmt.status_update(from_rcl_unnumbered, change).text.endswith("#RCL_12414100")
    assert fmt.act_published(from_rpw.model_copy(update={"act": ACT})).text.endswith(
        "#kadencja10druk1962 #RPW_29075_2026"
    )


def test_senate_position_is_rendered_from_its_position_field(process_1962: ProcessDetail) -> None:
    senate = next(
        s for s in flatten_stages(process_1962.stages) if s.stage_type == "SenatePosition"
    )
    odd = senate.model_copy(update={"position": "odroczył rozpatrywanie"})
    fmt = MessageFormatter("ru")

    known = fmt.status_update(bill_of(process_1962), change_of("1962", [senate])).text
    unknown = fmt.status_update(bill_of(process_1962), change_of("1962", [odd])).text

    assert "Сенат внёс поправки" in known and "(druk nr 2994)" in known
    assert "odroczył rozpatrywanie" in unknown  # unknown positions pass through verbatim


def test_voting_stage_shows_totals_pdf_link_and_club_breakdown(
    process_1962: ProcessDetail,
) -> None:
    voting = next(s for s in flatten_stages(process_1962.stages) if s.stage_type == "Voting")
    assert voting.voting is not None
    clubs = (
        ClubVotes(club="KO", yes=152),
        ClubVotes(club="PSL-TD", yes=31),
        ClubVotes(club="Lewica", yes=21),
        ClubVotes(club="Polska2050", yes=13),
        ClubVotes(club="Centrum", yes=12),
        ClubVotes(club="PiS", abstain=178, absent=6),
        ClubVotes(club="Konfederacja", no=1),
    )
    enriched = voting.model_copy(
        update={"voting": voting.voting.model_copy(update={"clubs": clubs})}
    )
    fmt = MessageFormatter("ru")

    totals_only = fmt.status_update(bill_of(process_1962), change_of("1962", [voting])).text
    with_clubs = fmt.status_update(bill_of(process_1962), change_of("1962", [enriched])).text

    assert "239 за, 1 против, 199 воздержались" in totals_only
    assert "votings/62/108/pdf" in totals_only and "За: KO" not in totals_only
    assert "За: KO 152, PSL-TD 31, Lewica 21, Polska2050 13, …" in with_clubs
    assert "Против: Konfederacja 1" in with_clubs and "Воздержались: PiS 178" in with_clubs
    assert_telegram_html(with_clubs)


def test_president_stages_and_committee_referrals_have_labels(process_3039: ProcessDetail) -> None:
    signed = Stage(
        stage_name="Podpisanie", stage_type="PresidentSignature", date=dt.date(2026, 8, 13)
    )
    veto = Stage(stage_name="Wniosek Prezydenta (weto)", stage_type="Veto", print_number="2863")
    referral = Stage(
        stage_name="Skierowanie",
        stage_type="Referral",
        committee_code="ASW",
        committee_name="Komisja Administracji i Spraw Wewnętrznych",
    )
    bare = Stage(stage_name="Skierowanie", stage_type="Referral", committee_code="ASW")
    bill = bill_of(process_3039)

    text = (
        MessageFormatter("ru")
        .status_update(bill, change_of("3039", [signed, veto, referral, bare]))
        .text
    )

    assert "• 13.08.2026: ✍️ Президент подписал закон" in text
    assert "• ⛔ Президент наложил вето (druk nr 2863)" in text
    assert "• 📮 Направлен в комиссию: Komisja Administracji i Spraw Wewnętrznych (ASW)" in text
    assert "• 📮 Направлен в комиссию: ASW" in text
