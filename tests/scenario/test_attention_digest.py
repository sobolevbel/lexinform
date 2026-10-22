import datetime as dt

import pytest

from lexinform.adapters.telegram_format import MessageFormatter, length
from lexinform.errors import DeliveryUncertainError
from lexinform.models import (
    DIGEST_NUMBER,
    DIGEST_TERM,
    Publication,
    PublicationKind,
    PublicationStatus,
    iso_week,
)
from lexinform.ports import PublishResult
from lexinform.services.digest import DigestService
from tests.formatting import assert_telegram_html
from tests.harness import CHANNEL, World


def _busy_week() -> tuple[World, DigestService]:
    w = World(reader_attention="on")
    for number in range(3000, 3040):
        w.add_bill(str(number), "Projekt ustawy o cudzoziemcach " + "z długim tytułem " * 8)
    w.run(max_publish=50, max_analyze=50)
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    return w, service


@pytest.mark.parametrize(("language", "more"), [("ru", "и ещё 32"), ("en", "and 32 more")])
def test_a_busy_week_is_one_message_that_says_how_many_it_left_out(
    language: str, more: str
) -> None:
    w, service = _busy_week()
    week = service.build(iso_week(w.clock.now().date()))
    formatter = MessageFormatter(
        language, today=lambda: w.clock.now().date(), channel=CHANNEL, attention_enabled=True
    )

    parts = formatter.digest_layout(week)

    assert len(parts) == 1 and length(parts[0].text) <= 4096
    assert_telegram_html(parts[0].text)
    assert more in parts[0].text


def test_uncertain_part_is_never_resent_and_audited_retry_keeps_all_part_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w, service = _busy_week()
    ref = iso_week(w.clock.now().date())
    delivered = w.publisher.publish_prepared
    count = 0

    def interrupt(publication: Publication) -> PublishResult:
        nonlocal count
        count += 1
        if count == 1:
            raise DeliveryUncertainError("connection lost")
        return delivered(publication)

    with monkeypatch.context() as patch:
        patch.setattr(w.publisher, "publish_prepared", interrupt)
        with pytest.raises(DeliveryUncertainError):
            service.publish(ref)
    root = w.repo.get_publication(
        DIGEST_TERM, DIGEST_NUMBER, PublicationKind.DIGEST, CHANNEL, ref=ref
    )
    assert root is not None and root.delivery is not None
    parts = [w.repo.publication_by_id(identity) for identity in root.delivery.digest_parts]
    assert len(parts) == 1 and parts[0] is not None
    assert parts[0].status is PublicationStatus.UNKNOWN
    before = len(w.publisher.texts(PublicationKind.DIGEST))
    saved = [
        part.delivery.rendered_text
        for part in parts
        if part is not None and part.delivery is not None
    ]
    w.repo.prune_history(before=w.clock.now() + dt.timedelta(days=1), memo_before=w.clock.now())
    w.repo.restore(w.repo.dump())

    blocked = service.publish(ref)

    assert blocked.failed and len(w.publisher.texts(PublicationKind.DIGEST)) == before
    assert parts[0].id is not None
    w.command(f"/delivery {parts[0].id} retry")
    w.run(discover=False, track=False, max_analyze=0, max_publish=0)
    finished = service.publish(ref)
    assert not finished.failed
    assert w.publisher.texts(PublicationKind.DIGEST) == saved
    assert service.publish(ref).note == "this week is in the channel already"
