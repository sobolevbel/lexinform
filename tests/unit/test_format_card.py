"""The card: what it says, how it escapes the model's words, its tags, and how it shrinks to the
4096-character limit. The "alternative bill" reply belongs here too — it is the card's thread."""

import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MESSAGE_LIMIT, MessageFormatter
from lexinform.models import PrintInfo, ProcessDetail, Stage
from tests.fakes import joint_record, make_analysis, make_comparison
from tests.formatting import ACT, TODAY, assert_telegram_html, bill_of, consulted
from tests.harness import act


def test_card_contains_every_section(process_3039: ProcessDetail, print_3039: PrintInfo) -> None:
    text = MessageFormatter("ru").new_bill(bill_of(process_3039), print_3039).text

    assert_telegram_html(text)
    assert text.startswith(
        f"📜 <b>Новый законопроект — druk nr 3039</b>\n\n<b>{process_3039.title}"
    )
    assert "🔴 <b>Важность:</b> ●●●●● 5/5" in text
    assert "О чём проект" in text and "Ключевые изменения" in text
    # The stage the bill stands on, then the referral under it; translated, the body stays Polish.
    assert "Стадия:</b> направлен на I чтение (03.09.2026) · направлен в комиссию ASW" in text
    assert "PrzebiegProc.xsp?nr=3039" in text and "prints/3039/3039.pdf" in text
    assert "#kadencja10druk3039 #важность5 #легализация #kadencja10" in text


def test_joint_bill_reply_names_the_thread_and_carries_both_tags(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    primary = bill_of(process_3039)
    other = process_3039.model_copy(
        update={
            "number": "3050",
            "title": "Rządowy projekt ustawy o zmianie ustawy o udzielaniu cudzoziemcom ochrony",
            "prints_considered_jointly": ("3039", "3051"),
            "document_date": dt.date(2026, 9, 2),
        }
    )

    reply = bill_of(other, joint=joint_record(["3039"], make_comparison()))

    text = MessageFormatter("ru").joint_bill(reply, primary, print_3039).text

    assert_telegram_html(text)
    assert text.startswith(
        "🔀 <b>Альтернативный проект того же закона — druk nr 3050</b>\n\n<b>Rządowy projekt"
    )
    assert "Рассматривается совместно с druk 3039, 3051:" in text  # the card's print first
    assert "Инициатор:</b> правительственный\n📄 <b>Дата druku:</b> 02.09.2026" in text
    # What this print does and how it differs; the score and the category stay the card's.
    assert "О чём проект" in text and "Чем отличается от других проектов" in text
    assert "Важность" not in text
    assert "PrzebiegProc.xsp?nr=3050" in text
    # Its own tag, the card's topic tags and the thread's: a search for any of the three
    # finds the reply, not only the card that carries the analysis.
    assert text.endswith("#kadencja10druk3050 #важность5 #легализация #kadencja10druk3039")


def test_a_joint_reply_walks_the_road_of_its_own_print_not_the_cards(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    """The step is the thread's — jointly considered prints move as one from the referral — but
    the beginning of the road is each print's own: druk 316 is the President's and never passed
    through a government plan or RCL, and under druk 1929's card it was saying it had.
    """
    # A government bill (it has an RM number): its road starts on the wykaz and on RCL.
    primary = bill_of(process_3039.model_copy(update={"rcl_num": "RM-0610-188-25"}))
    presidents = process_3039.model_copy(
        update={
            "number": "316",
            "title": (
                "Przedstawiony przez Prezydenta Rzeczypospolitej Polskiej projekt ustawy"
                " o asystencji osobistej osób z niepełnosprawnościami"
            ),
            "prints_considered_jointly": ("3039",),
            "rcl_num": None,
        }
    )

    reply = bill_of(presidents, joint=joint_record(["3039"], make_comparison()))

    text = MessageFormatter("ru").joint_bill(reply, primary, print_3039).text
    card = MessageFormatter("ru").new_bill(primary, print_3039).text

    assert "Путь:</b> Сейм ✓" in text and "план" not in text.split("Путь")[1]
    assert "Путь:</b> план ✓ → RCL ✓ → Сейм ✓" in card  # the card's own road is unchanged


def test_card_escapes_model_output_and_notes_partial_text(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    analysis = make_analysis()
    analysis.summary = "Ссылка <b>жирный</b> & амперсанд"

    text = (
        MessageFormatter("ru")
        .new_bill(bill_of(process_3039, analysis, truncated=True), print_3039)
        .text
    )

    assert_telegram_html(text)
    assert "&lt;b&gt;жирный&lt;/b&gt; &amp; амперсанд" in text
    assert "неполном тексте" in text


@pytest.mark.parametrize(
    ("n_changes", "change_len", "summary_len"), [(12, 400, 3000), (30, 200, 500), (1, 10, 20000)]
)
def test_card_shrinks_the_flexible_blocks_to_the_limit(
    process_3039: ProcessDetail,
    print_3039: PrintInfo,
    n_changes: int,
    change_len: int,
    summary_len: int,
) -> None:
    analysis = make_analysis()
    analysis.summary = "с" * summary_len
    analysis.key_changes = ["и" * change_len] * n_changes

    text = MessageFormatter("ru").new_bill(bill_of(process_3039, analysis), print_3039).text

    assert_telegram_html(text)
    assert "PrzebiegProc.xsp?nr=3039" in text and "#kadencja10druk3039" in text  # fixed parts


def test_card_groups_short_facts_and_separates_paragraphs(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    text = MessageFormatter("ru").new_bill(bill_of(process_3039), print_3039).text

    practical = text.index("Что это значит на практике")
    affected = text.index("Кого касается")
    effective = text.index("Вступление в силу")
    stage = text.index("Стадия:")
    applicant = text.index("Инициатор:")
    assert "\n\n" in text[practical:affected]
    assert "\n\n" in text[affected:effective]
    assert "\n\n" in text[effective:stage]
    assert "\n\n" not in text[stage:applicant]  # stage and applicant stay on adjacent lines


def test_labels_and_date_format_follow_the_language(
    process_3039: ProcessDetail, print_3039: PrintInfo
) -> None:
    ru = MessageFormatter("ru").new_bill(bill_of(process_3039), print_3039).text
    en = MessageFormatter("en").new_bill(bill_of(process_3039), print_3039).text

    assert "\n📄 <b>Дата druku:</b> 03.08.2026" in ru  # the date has its own line
    assert "New bill" in en and "#importance5" in en
    assert "Print date:</b> 2026-08-03" in en


def test_formatter_dates_from_its_own_clock_when_no_day_is_given(
    process_3039: ProcessDetail,
) -> None:
    bill = bill_of(
        process_3039,
        submission=consulted(number="RPW/1/2026", consultation_end=dt.date(2026, 9, 20)),
    )
    during = MessageFormatter("ru", today=lambda: dt.date(2026, 9, 10))
    after = MessageFormatter("ru", today=lambda: dt.date(2026, 10, 1))

    open_tags = during.new_bill(bill, None).text.splitlines()[-1]
    closed_tags = after.new_bill(bill, None).text.splitlines()[-1]

    assert "#консультации" in open_tags and "#консультации" not in closed_tags


def test_card_tags_open_consultations_and_ukraine(process_3039: ProcessDetail) -> None:
    bill = bill_of(
        process_3039,
        submission=consulted(number="RPW/1/2026", consultation_end=dt.date(2026, 9, 20)),
        prefilter_hits=["cudzoziemcy", "text:obywatele_ukrainy"],
    )

    tags = (
        MessageFormatter("ru")
        .new_bill(bill, None, today=dt.date(2026, 9, 10))
        .text.splitlines()[-1]
    )

    assert tags == "#kadencja10druk3039 #важность5 #легализация #Украина #консультации #kadencja10"


def test_closed_consultation_loses_its_tag_and_ukraine_is_read_from_the_title(
    process_3039: ProcessDetail,
) -> None:
    ukraine = process_3039.model_copy(
        update={"title": "Rządowy projekt ustawy o pomocy obywatelom Ukrainy"}
    )
    bill = bill_of(
        ukraine, submission=consulted(number="RPW/1/2026", consultation_end=dt.date(2026, 9, 20))
    )

    tags = (
        MessageFormatter("ru")
        .new_bill(bill, None, today=dt.date(2026, 9, 21))
        .text.splitlines()[-1]
    )

    assert tags == "#kadencja10druk3039 #важность5 #легализация #Украина #kadencja10"


def test_a_consultation_that_is_over_says_so_and_stops_inviting_opinions(
    process_3039: ProcessDetail,
) -> None:
    """A Sejm consultation has a start and an end. Rendered as a plain range next to the form
    link, a window that closed a year ago reads as an invitation (it is not)."""
    bill = bill_of(
        process_3039,
        submission=consulted(
            number="RPW/2793/2025",
            consultation_start=dt.date(2025, 1, 24),
            consultation_end=dt.date(2025, 2, 23),
        ),
    )
    formatter = MessageFormatter("ru")

    while_open = formatter.new_bill(bill, None, today=dt.date(2025, 2, 1)).text
    once_closed = formatter.new_bill(bill, None, today=dt.date(2026, 9, 11)).text

    assert "<b>Общественные консультации:</b> 24.01.2025 — 23.02.2025" in while_open
    assert ">анкета на сайте Сейма</a>" in while_open
    assert "<b>Общественные консультации:</b> завершились 23.02.2025" in once_closed
    assert "страница консультаций" in once_closed  # the page stays, the invitation goes
    assert "анкета на сайте Сейма" not in once_closed
    assert "24.01.2025" not in once_closed  # the end date is the fact that matters


def test_fixed_blocks_alone_over_the_limit_are_cut_at_a_line_boundary(
    process_3039: ProcessDetail,
) -> None:
    long_title = " ".join(["Ustawa o zmianie ustawy o cudzoziemcach"] * 120)
    summary = process_3039.model_copy(update={"title": long_title})
    bill = bill_of(summary, make_analysis(score=4))

    text = MessageFormatter("ru").new_bill(bill, None).text

    assert len(text) <= MESSAGE_LIMIT
    assert_telegram_html(text)


def test_the_card_cites_the_act_the_way_everyone_else_does(process_3039: ProcessDetail) -> None:
    """Dz.U. 2026 poz. 1099 is how the law is named in every office and every other text; it
    used to live only in the publication notice, months down the replies."""
    text = MessageFormatter("ru").new_bill(bill_of(process_3039, act=ACT), None, today=TODAY).text

    assert "📰 <b>Публикация:</b> Dz.U. 2026 poz. 1099 (опубликован 18.08.2026)" in text


def test_a_card_for_a_bill_already_in_force_says_so_and_links_the_act(
    process_1962: ProcessDetail,
) -> None:
    """Discovery keeps such a bill off the channel, but `/republish` and an inherited card can
    still put one there, and three empty lines would say nothing about being over."""
    published = process_1962.model_copy(update={"eli": "DU/2026/1100"})
    bill = bill_of(published, act=act(entry_into_force=dt.date(2026, 9, 1)))

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert "Законопроект: процесс завершён — druk nr 1962" in text
    assert "Уже действует с</b> 01.09.2026" in text
    assert "Что дальше" not in text and "Что можно сделать" not in text
    assert "Текст закона (PDF)" in text


def test_a_bill_waiting_only_for_its_vacatio_legis_is_not_called_finished(
    process_1962: ProcessDetail,
) -> None:
    published = process_1962.model_copy(update={"eli": "DU/2026/1100"})
    bill = bill_of(published, act=act(entry_into_force=dt.date(2026, 11, 19)))

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert "Новый законопроект" in text
    assert "Что дальше:</b> вступление в силу 19.11.2026" in text
    assert "Уже действует" not in text


def test_a_stage_tree_the_model_cannot_read_is_not_called_finished(
    process_1962: ProcessDetail,
) -> None:
    """`next_phase` gives up on a stage type it does not know, and there is then nothing to say
    about how the road ended. A header claiming it ended would be a guess, so the card keeps its
    own and simply leaves the three step lines out."""
    unknown = Stage(stage_name="Nowy etap", stage_type="SomethingNew", date=dt.date(2026, 9, 4))
    running = process_1962.model_copy(
        update={"stages": (unknown,), "closure_date": None, "passed": None}
    )
    bill = bill_of(running)

    text = MessageFormatter("ru").new_bill(bill, None, today=TODAY).text

    assert "📜 <b>Новый законопроект — druk nr 1962</b>" in text
    assert "процесс завершён" not in text
    assert "Что дальше" not in text


def test_a_card_says_the_veto_stood_when_it_did(process_1962: ProcessDetail) -> None:
    """The API leaves `passed` true on a law the President's veto killed, so the closure branch
    of `_ended_line` never fired: the card kept the header «Новый законопроект» and lost its path,
    its next step and its action line without a word about why. The only trace was the «Стадия»
    line — a Polish procedural sentence in the row a reader scans for a stage name."""
    vetoed = process_1962.model_copy(
        update={
            "stages": (
                Stage(stage_name="Wniosek Prezydenta (weto)", stage_type="Veto"),
                Stage(
                    stage_name="Rozpatrywanie na forum Sejmu wniosku Prezydenta",
                    stage_type="PresidentMotionConsideration",
                    decision="nie uchwalona ponownie",
                    date=dt.date(2026, 9, 11),
                ),
                Stage(
                    stage_name="Ustawa nie uchwalona ponownie po wecie Prezydenta",
                    stage_type="End",
                ),
            ),
            "passed": True,
        }
    )

    text = MessageFormatter("ru").new_bill(bill_of(vetoed), None, today=TODAY).text

    assert "📜 <b>Законопроект: процесс завершён — druk nr 1962</b>" in text
    assert "Сейм не отклонил вето Президента (нужно 3/5 голосов): закон не принят." in text
    assert "Что дальше" not in text and "Что можно сделать" not in text


def test_a_card_says_the_veto_stood_even_when_the_end_node_denies_it(
    process_1962: ProcessDetail,
) -> None:
    """Eight of the fifteen processes of term 10 the Sejm failed to re-adopt keep `End` =
    "Uchwalono" beside `passed` = true — druki 410, 643, 865, 935, 1109, 1110, 1131 and 1600.
    Reading the rename alone left them with no ending line at all."""
    vetoed = process_1962.model_copy(
        update={
            "stages": (
                Stage(stage_name="Wniosek Prezydenta (weto)", stage_type="Veto"),
                Stage(
                    stage_name="Rozpatrywanie na forum Sejmu wniosku Prezydenta",
                    stage_type="PresidentMotionConsideration",
                    decision="nie uchwalona ponownie",
                    date=dt.date(2026, 3, 27),
                ),
                Stage(stage_name="Uchwalono", stage_type="End"),
            ),
            "passed": True,
        }
    )

    text = MessageFormatter("ru").new_bill(bill_of(vetoed), None, today=TODAY).text

    assert "Сейм не отклонил вето Президента (нужно 3/5 голосов): закон не принят." in text


def test_a_card_names_the_tribunals_ruling(process_1962: ProcessDetail) -> None:
    """Ten bills of term 10 stand at `PresidentToTribunal`. The ruling that answers them was an
    unrecognised stage, so the card said nothing at all about the road having ended."""
    ruled = process_1962.model_copy(
        update={
            "stages": (
                Stage(
                    stage_name="Prezydent skierował ustawę do Trybunału Konstytucyjnego",
                    stage_type="PresidentToTribunal",
                    date=dt.date(2026, 6, 1),
                ),
                Stage(
                    stage_name="Wyrok Trybunału Konstytucyjnego",
                    stage_type="ConstitutionalTribunalRuling",
                    date=dt.date(2026, 8, 20),
                ),
            ),
            "passed": True,
        }
    )

    text = MessageFormatter("ru").new_bill(bill_of(ruled), None, today=TODAY).text

    assert "Законопроект: процесс завершён" in text
    assert "Конституционный трибунал вынес решение по закону." in text
