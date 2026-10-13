import datetime as dt

from lexinform.models import OutcomeStatus, PublicationKind, RunMode
from tests.harness import COMMITTEE_STAGES, REFERRED, World


def test_operator_renames_with_audit_and_old_name_search_without_model_calls() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    requests = len(w.llm.contexts)
    first = w.command("/name 3039 Таймер решения по ВНЖ | Срок решения в тексте")
    w.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)
    second = w.command("/name 3039 Срок решения по ВНЖ | Нейтральное имя")

    w.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)
    w.repo.restore(w.repo.dump())

    editorial = w.bill("3039").editorial
    assert (
        editorial.mnemonic is not None and editorial.mnemonic.command_update_id == second.update_id
    )
    assert editorial.previous[0].command_update_id == first.update_id
    assert w.repo.search("ТАЙМЕР", limit=5)[0].number == "3039"
    assert len(w.llm.contexts) == requests
    w.set_stages("3039", COMMITTEE_STAGES)
    w.run()
    assert (
        "Прежнее имя: Таймер решения по ВНЖ" in w.publisher.texts(PublicationKind.STATUS_UPDATE)[-1]
    )
    assert w.bill("3039").editorial.announced_version == 2


def test_operator_priority_expires_and_cannot_hide_protected_facts() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    today = w.clock.now().date()
    incoming = w.command(f"/attention 3039 urgent until={today.isoformat()} | Короткое окно")

    w.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)

    priority = w.bill("3039").editorial.priority
    assert priority is not None and priority.command_update_id == incoming.update_id
    assert priority.urgent and priority.expires_at > w.clock.now()
    assert priority.expires_at.astimezone(dt.UTC).hour == 22
    w.command(f"/attention 3039 routine until={today.isoformat()} | Скрыть")
    w.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)
    assert w.replier.replies[-1][1].status is OutcomeStatus.HELP
    assert w.bill("3039").editorial.priority == priority


def test_operator_sees_why_a_service_frame_is_held_without_a_reader_message() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", REFERRED)
    w.run()
    w.command("/attention 3039")

    w.run(mode=RunMode.COMMANDS, discover=False, track=False, max_analyze=0, max_publish=0)

    note = w.replier.replies[-1][1].note
    assert "editorial_frame" in note and "skipped" in note
    assert not w.publisher.texts(PublicationKind.STATUS_UPDATE)
