from typing import Literal

import pytest
from bs4 import BeautifulSoup

from lexinform.models import Bill, PrintInfo, PublicationKind, PublicationStatus
from lexinform.telegram_text import split_html
from tests.fakes import make_analysis
from tests.formatting import assert_telegram_html
from tests.harness import World


def plain(text: str) -> str:
    return BeautifulSoup(text, "html.parser").get_text()


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_long_card_preserves_all_facts_with_durable_physical_parts(
    mode: Literal["off", "shadow", "on"],
) -> None:
    w = World(reader_attention=mode)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    analysis = make_analysis()
    analysis.key_changes = ["200 PLN, nie 300 PLN; art. 15 ust. 2; stare wnioski. " * 100]
    w.llm.script["3039"] = analysis
    report = w.run()
    root = w.publication("3039")
    assert root is not None and root.delivery is not None
    assert root.status is PublicationStatus.SENT
    children = [w.repo.publication_by_id(identity) for identity in root.delivery.continuation_ids]
    assert children and all(
        child is not None and child.status is PublicationStatus.SENT for child in children
    )
    info = (
        PrintInfo.model_validate_json(root.delivery.print_json)
        if root.delivery.print_json
        else None
    )
    expected = w.formatter.new_bill(
        Bill.model_validate_json(root.delivery.bill_json), info, today=root.delivery.today
    ).text
    texts = [root.delivery.rendered_text] + [
        child.delivery.rendered_text
        for child in children
        if child is not None and child.delivery is not None
    ]
    assert "".join(plain(text.split("\n\n", 1)[1]) for text in texts) == plain(expected)
    assert all(change.strip() in expected for change in analysis.key_changes)
    for text in texts:
        assert_telegram_html(text)
    assert report.message_parts_sent == len(children)
    assert all(
        child is not None and child.delivery is not None and child.delivery.reply_to is not None
        for child in children
    )
    w.repo.restore(w.repo.dump())
    w.run()
    assert len(w.publisher.texts(PublicationKind.NEW_BILL)) == 1
    assert len(w.publisher.texts(PublicationKind.MESSAGE_PART)) == len(children)


@pytest.mark.parametrize("failure", ["failed", "unknown"])
@pytest.mark.parametrize("resolution", ["retry", "confirm", "dismiss"])
def test_interrupted_part_keeps_parent_and_text_across_restart(
    failure: str, resolution: str
) -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    analysis = make_analysis()
    analysis.practical_impact = (
        "Старые заявления не подавать заново; срок 1 декабря; 200 PLN; art. 15. " * 170
    )
    w.llm.script["3039"] = analysis
    if failure == "failed":
        w.publisher.fail_continuation_attempt = 1
    else:
        w.publisher.unknown_continuation_attempt = 1
    w.run()
    root = w.publication("3039")
    assert root is not None and root.delivery is not None
    identity = root.delivery.continuation_ids[0]
    part = w.repo.publication_by_id(identity)
    assert part is not None and part.delivery is not None
    assert part.status.value == failure
    saved = part.delivery
    assert saved.reply_to == root.message_id
    w.repo.restore(w.repo.dump())
    if failure == "failed":
        w.publisher.fail_continuation_attempt = None
        w.run()
        assert len(w.publisher.texts(PublicationKind.NEW_BILL)) == 1
        finished = w.repo.publication_by_id(identity)
        assert finished is not None and finished.status is PublicationStatus.SENT
        return
    w.run()
    assert not w.publisher.texts(PublicationKind.MESSAGE_PART)
    w.command(
        f"/delivery {identity} {resolution}"
        + (
            " 888"
            if resolution == "confirm"
            else " no longer useful"
            if resolution == "dismiss"
            else ""
        )
    )
    w.run()
    recovered = w.repo.publication_by_id(identity)
    assert recovered is not None and recovered.delivery == saved
    assert recovered.status is (
        PublicationStatus.DISMISSED if resolution == "dismiss" else PublicationStatus.SENT
    )
    if resolution == "dismiss":
        assert not w.publisher.texts(PublicationKind.MESSAGE_PART)
        for child in root.delivery.continuation_ids:
            abandoned = w.repo.publication_by_id(child)
            assert abandoned is not None and abandoned.status is PublicationStatus.DISMISSED
    elif resolution == "confirm":
        following = w.repo.publication_by_id(root.delivery.continuation_ids[1])
        assert following is not None and following.delivery is not None
        assert following.delivery.reply_to == 888
    else:
        assert w.publisher.texts(PublicationKind.MESSAGE_PART)[0] == saved.rendered_text
    assert len(w.publisher.texts(PublicationKind.NEW_BILL)) == 1


def test_split_preserves_entities_nested_links_and_supplementary_unicode() -> None:
    text = "<b>Область: <i>" + ("🙂 &amp; 200 PLN, не 300 PLN. " * 150) + "</i></b>"
    text += (
        '<a href="https://example.test/article?x=1&amp;y=2">' + ("art. 15 ust. 2 " * 300) + "</a>"
    )
    parts = split_html(text)
    assert len(parts) > 1
    assert "".join(map(plain, parts)) == plain(text)
    for part in parts:
        assert_telegram_html(part)
