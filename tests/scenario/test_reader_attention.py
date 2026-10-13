import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.attention import assess_attention
from lexinform.models import (
    Editorial,
    Mnemonic,
    PublicationKind,
    Stage,
    StatusChange,
    valid_mnemonic,
)
from tests.fakes import make_analysis
from tests.harness import COMMITTEE_STAGES, TERM, World


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Пять слов это слишком много",
        "Имя\nпроекта",
        "<b>Имя</b>",
        "#Имя",
        "https://example.test",
        "Два  пробела",
    ],
)
def test_invalid_names_never_block_analysis(name: str) -> None:
    w = World(
        llm_script={
            "3039": make_analysis().model_copy(
                update={
                    "mnemonic_candidate": name,
                    "mnemonic_fallback": "Таймер решения по ВНЖ",
                    "mnemonic_basis": "Предельный срок решения",
                }
            )
        }
    )
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")

    w.run()

    bill = w.bill("3039")
    assert bill.reader_name == "Таймер решения по ВНЖ"
    assert w.card_id("3039") is not None
    assert "Таймер решения по ВНЖ" in w.formatter.new_bill(bill, None).text
    assert not valid_mnemonic(name)


def test_reanalysis_proposes_name_and_preserves_chosen_name_through_restore() -> None:
    answer = make_analysis().model_copy(
        update={
            "mnemonic_candidate": "Ценник заявления на гражданство",
            "mnemonic_basis": "Изменён сбор",
        }
    )
    w = World(llm_script={"3039": answer})
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    record = w.bill("3039").analysis
    assert record is not None

    w.repo.save_analysis(
        TERM,
        "3039",
        record.model_copy(
            update={
                "analysis": answer.model_copy(update={"mnemonic_candidate": "Сбор за гражданство"})
            }
        ),
    )
    w.repo.restore(w.repo.dump())

    bill = w.bill("3039")
    assert bill.reader_name == "Ценник заявления на гражданство"
    assert bill.editorial.proposed is not None
    assert bill.editorial.proposed.name == "Сбор за гражданство"
    assert w.repo.search("ЦЕННИК", limit=5)[0].number == "3039"


def test_editorial_name_is_inherited_only_on_confirmed_link() -> None:
    w = World()
    for number in ("3039", "3040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    mnemonic = Mnemonic(
        name="Таймер решения по ВНЖ", language="ru", basis="Срок решения", selected_at=w.clock.now()
    )
    w.repo.save_editorial(TERM, "3039", Editorial(mnemonic=mnemonic))

    w.repo.link_bills(TERM, "3039", "3040")
    w.repo.restore(w.repo.dump())

    assert w.bill("3040").editorial == w.bill("3039").editorial


def test_attention_is_persisted_in_shadow_without_changing_delivery() -> None:
    w = World()
    for number in ("3039", "3040"):
        w.add_bill(number, "Projekt ustawy o cudzoziemcach")
    w.run()
    for number in ("3039", "3040"):
        w.set_stages(number, COMMITTEE_STAGES)

    report = w.run()
    w.repo.restore(w.repo.dump())

    assert (report.updates, report.update_events) == (1, 2)
    pub = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert pub is not None and pub.delivery is not None and pub.delivery.attention is not None
    assert pub.delivery.attention.importance == "routine"
    assert pub.delivery.policy_version == "legacy"


def test_event_priority_does_not_use_project_score_and_unknown_is_protected() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    bill = w.bill("3039")
    change = StatusChange(
        term=TERM,
        number=bill.number,
        old_fingerprint="old",
        new_fingerprint="new",
        new_stages=[Stage(stage_type="Referral", stage_name="Skierowanie", committee_code="ASW")],
        detected_at=w.clock.now(),
    )

    routine = assess_attention(
        bill, PublicationKind.STATUS_UPDATE, now=w.clock.now(), change=change
    )
    important = assess_attention(
        bill,
        PublicationKind.STATUS_UPDATE,
        now=w.clock.now(),
        change=change.model_copy(update={"content_changed": True}),
    )
    unknown = assess_attention(
        bill,
        PublicationKind.STATUS_UPDATE,
        now=w.clock.now(),
        change=change.model_copy(update={"new_stages": []}),
    )

    assert routine.importance == "routine" and not routine.protected
    assert important.importance == "important" and important.protected
    assert unknown.importance == "uncertain" and unknown.protected
    assert (
        "Нужно действовать"
        not in MessageFormatter("ru", attention_enabled=True)
        .status_update(bill, change, today=dt.date(2026, 9, 7))
        .text
    )
