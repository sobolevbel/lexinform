"""Public consultations: the deadline reminder and the notice that the opinions were published."""

import datetime as dt

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.models import PublicationKind, PublicationStatus
from tests.harness import RPW, World, submission


def test_reminder_is_posted_three_days_before_the_deadline() -> None:
    w = World()  # clock: 2026-09-07
    w.gateway.submissions.append(submission(consultation_end=dt.date(2026, 9, 20)))
    first = w.run()
    w.clock.advance(days=10)  # 2026-09-17: three days left

    report = w.run()

    assert first.consultation_reminders == 0
    assert report.consultation_reminders == 1
    bill, reply_to, today = w.publisher.consultations[0]
    assert (bill.number, reply_to, today) == (RPW, w.card_id(RPW), dt.date(2026, 9, 17))


def test_reminder_renders_the_countdown() -> None:
    w = World()
    w.gateway.submissions.append(submission(consultation_end=dt.date(2026, 9, 20)))
    w.run()
    bill = w.bill(RPW)

    ahead = MessageFormatter("ru").consultation_deadline(bill, today=dt.date(2026, 9, 17)).text
    last_day = MessageFormatter("ru").consultation_deadline(bill, today=dt.date(2026, 9, 20)).text

    assert "Консультации заканчиваются — RPW/29075/2026" in ahead
    assert "до 20.09.2026 · осталось дней: 3" in ahead
    assert "#консультации #важность5 #легализация #RPW_29075_2026" in ahead
    assert "сегодня последний день" in last_day


def test_reminder_is_posted_once_and_never_late() -> None:
    w = World()
    w.gateway.submissions.append(submission(consultation_end=dt.date(2026, 9, 20)))
    w.run()
    w.clock.advance(days=10)
    w.run()

    w.clock.advance(days=1)
    next_day = w.run()
    w.clock.advance(days=5)  # 2026-09-23: a consultation that ended two days ago appears
    w.gateway.submissions.append(
        submission(number="RPW/1/2026", consultation_end=dt.date(2026, 9, 21))
    )
    late = w.run()

    assert next_day.consultation_reminders == 0
    assert late.consultation_reminders == 0
    assert len(w.publisher.consultations) == 1


def test_failed_reminder_is_retried_next_run() -> None:
    w = World()  # clock: 2026-09-07
    w.gateway.submissions.append(submission(consultation_end=dt.date(2026, 9, 11)))
    w.run()  # four days ahead: not due yet
    w.publisher.fail_on = {RPW}
    w.clock.advance(days=1)  # three days ahead: due

    failed = w.run()
    w.publisher.fail_on = set()
    w.clock.advance(days=1)
    retried = w.run()

    assert failed.consultation_reminders == 0 and failed.errors
    assert retried.consultation_reminders == 1
    assert len(w.publisher.consultations) == 1


def test_published_opinions_of_a_numbered_print_are_announced_once() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.submissions.append(submission(number="RPW/26666/2026", print_number="3039"))
    first = w.run()
    w.gateway.submissions[0] = submission(
        number="RPW/26666/2026", print_number="3039", consultation_results=True
    )
    w.clock.advance(days=1)

    report = w.run()
    w.clock.advance(days=1)
    again = w.run()

    assert first.consultation_results_posted == 0
    assert report.consultation_results_posted == 1
    bill, reply_to = w.publisher.consultation_results[0]
    assert (bill.number, reply_to) == ("3039", w.card_id("3039"))
    assert bill.submission is not None and bill.submission.consultation_results
    assert again.consultation_results_posted == 0 and len(w.publisher.consultation_results) == 1


def test_failed_results_notice_is_retried_on_the_next_run() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.submissions.append(submission(number="RPW/26666/2026", print_number="3039"))
    w.run()
    w.gateway.submissions[0] = submission(
        number="RPW/26666/2026", print_number="3039", consultation_results=True
    )
    w.publisher.fail_on = {"3039"}
    w.clock.advance(days=1)
    failed = w.run()
    w.publisher.fail_on = set()
    w.clock.advance(days=1)

    retried = w.run()

    assert (failed.consultation_results_posted, retried.consultation_results_posted) == (0, 1)
    assert len(w.publisher.consultation_results) == 1
    pub = w.publication("3039", PublicationKind.CONSULTATION_RESULTS)
    assert pub is not None and (pub.status, pub.attempts) == (PublicationStatus.SENT, 1)


def test_results_notice_links_the_consultation_page() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.gateway.submissions.append(
        submission(number="RPW/26666/2026", print_number="3039", consultation_results=True)
    )
    w.run()

    text = MessageFormatter("ru").consultation_results(w.bill("3039")).text

    assert "Опубликованы мнения из консультаций — druk nr 3039" in text
    assert "NrProjektu=RPW/26666/2026" in text


def test_pre_print_bill_discovered_with_published_opinions_gets_no_notice() -> None:
    w = World()
    w.gateway.submissions.append(submission(consultation_results=True))

    report = w.run()

    assert (report.published, report.consultation_results_posted) == (1, 0)


def test_pre_print_bill_gets_the_notice_when_its_opinions_appear() -> None:
    w = World()
    w.gateway.submissions.append(submission())
    w.run()
    w.gateway.submissions[0] = submission(consultation_results=True)
    w.clock.advance(days=1)

    report = w.run()

    assert report.consultation_results_posted == 1
    assert w.publisher.consultation_results[0][0].number == RPW
