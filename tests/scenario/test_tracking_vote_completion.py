import datetime as dt

import pytest

from lexinform.models import (
    PlenaryVoting,
    PublicationKind,
    PublicationStatus,
    Stage,
    Vote,
    VotingSummary,
)
from tests.harness import REFERRED, World

PASSED = Stage(
    stage_name="III czytanie na posiedzeniu Sejmu",
    stage_type="SejmReading",
    date=dt.date(2026, 9, 10),
    decision="uchwalono",
    sitting_num=62,
)
RESULT = VotingSummary(yes=261, no=0, abstain=182, sitting=62, voting_number=16)
VOTE = PlenaryVoting(
    title="Sprawozdanie Komisji o projekcie ustawy (druki nr 3039 i 3100)",
    description="całość projektu ustawy",
    topic="głosowanie nad całością projektu.",
    date=dt.date(2026, 9, 10),
    result=RESULT,
)


@pytest.mark.parametrize("workers", [1, 4])
def test_passed_post_fetches_missing_vote_and_does_not_repeat_when_the_tree_catches_up(
    workers: int,
) -> None:
    w = World(workers=workers)
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.gateway.sitting_votings[62] = (VOTE,)
    w.gateway.votings[(62, 16)] = (Vote(mp=1, club="KO", vote="YES"),)
    w.set_stages("3039", (*REFERRED, PASSED))
    w.clock.advance(days=4)

    report = w.run()
    w.gateway.sitting_votings[62] = ()
    w.clock.advance(days=1)
    assert w.run().updates == 0
    voted = Stage(stage_name="Głosowanie", stage_type="Voting", date=PASSED.date, voting=RESULT)
    w.set_stages("3039", (*REFERRED, PASSED.model_copy(update={"children": (voted,)})))
    w.clock.advance(days=1)
    again = w.run()

    assert report.updates == 1 and again.updates == 0
    text = w.publisher.texts(PublicationKind.STATUS_UPDATE)[0]
    assert "Сейм принял закон" in text and "261 за" in text and "KO 1" in text
    assert len(w.publisher.updates) == 1


@pytest.mark.parametrize(
    "votings",
    [
        (VOTE.model_copy(update={"title": "Projekt ustawy (druk nr 30390)"}),),
        (VOTE.model_copy(update={"date": dt.date(2026, 9, 9)}),),
        (VOTE.model_copy(update={"description": "poprawka", "topic": "poprawka nr 1"}),),
        (VOTE.model_copy(update={"result": RESULT.model_copy(update={"sitting": 63})}),),
        (VOTE, VOTE.model_copy(update={"result": RESULT.model_copy(update={"voting_number": 17})})),
        (),
    ],
)
def test_passed_post_does_not_guess_a_vote(votings: tuple[PlenaryVoting, ...]) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.gateway.sitting_votings[62] = votings
    w.set_stages("3039", (*REFERRED, PASSED))
    w.clock.advance(days=4)

    report = w.run()

    assert report.updates == 1
    text = w.publisher.texts(PublicationKind.STATUS_UPDATE)[0]
    assert "Сейм принял закон" in text and "261 за" not in text


@pytest.mark.parametrize("fail_edit", [False, True])
def test_late_vote_edits_the_passed_message_and_retries_its_saved_snapshot(fail_edit: bool) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.set_stages("3039", (*REFERRED, PASSED))
    w.clock.advance(days=4)
    w.run()
    adoption = w.repo.list_status_changes(10, "3039")[-1]
    assert adoption.id is not None
    original = w.repo.get_update_publication(adoption.id, w.container.channel_id())
    assert original is not None
    w.gateway.sitting_votings[62] = (VOTE,)
    w.gateway.votings[(62, 16)] = (Vote(mp=1, club="KO", vote="YES"),)
    if fail_edit:
        w.publisher.fail_on.add("3039")
    w.clock.advance(days=1)

    w.run()
    completion = w.repo.list_status_changes(10, "3039")[-1]
    assert completion.id is not None
    post = w.repo.get_update_publication(completion.id, w.container.channel_id())
    assert post is not None and post.delivery is not None
    assert post.delivery.edit_message_id == original.message_id
    if fail_edit:
        assert post.status is PublicationStatus.FAILED
        w.publisher.fail_on.clear()
        w.touch("3039", w.clock.now(), title="Projekt ustawy o zupełnie innym przedmiocie")
        w.clock.advance(days=1)
        w.run()

    edits = [
        (out, identity)
        for out, identity in w.publisher.edited
        if out.kind is PublicationKind.STATUS_UPDATE
    ]
    assert len(w.publisher.updates) == 1 and len(edits) == 1
    out, identity = edits[0]
    assert identity == original.message_id
    assert "Сейм принял закон" in out.text and "261 за" in out.text and "KO 1" in out.text
    assert "Состоялось голосование" not in out.text
    assert "zupełnie innym przedmiocie" not in out.text
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    digest = service.build("2026-W37")
    assert len(digest.updates) == 1
    assert digest.updates[0].event == "passed" and digest.updates[0].message_id == identity
    w.clock.advance(days=1)
    assert w.run().updates == 0


@pytest.mark.parametrize("uncertain", [False, True])
def test_late_vote_is_queued_without_publishing_and_never_retries_unknown_work(
    uncertain: bool,
) -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.set_stages("3039", (*REFERRED, PASSED))
    w.clock.advance(days=4)
    w.run()
    w.gateway.sitting_votings[62] = (VOTE,)
    w.clock.advance(days=1)

    w.run(publish=False)
    completion = w.repo.list_status_changes(10, "3039")[-1]
    assert completion.id is not None
    post = w.repo.get_update_publication(completion.id, w.container.channel_id())
    assert post is not None and post.id is not None and post.delivery is not None
    assert post.status is PublicationStatus.QUEUED and post.delivery.edit_message_id is not None
    if uncertain:
        w.repo.mark_publication(post.id, PublicationStatus.UNKNOWN, count_attempt=False)
    w.clock.advance(days=1)
    w.run()

    edits = [out for out, _ in w.publisher.edited if out.kind is PublicationKind.STATUS_UPDATE]
    assert len(edits) == (0 if uncertain else 1)
    assert len(w.publisher.updates) == 1


def test_a_late_vote_edit_does_not_reannounce_last_weeks_adoption_in_the_digest() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.set_stages("3039", (*REFERRED, PASSED))
    w.clock.advance(days=4)
    w.run()
    w.gateway.sitting_votings[62] = (VOTE,)
    w.clock.advance(days=4)

    w.run()

    service = w.container.digest_service(dry_run=False)
    assert service is not None
    assert len(service.build("2026-W37").updates) == 1
    assert service.build("2026-W38").updates == ()


@pytest.mark.parametrize("workers", [1, 4])
def test_attention_routes_only_proven_late_vote_details_to_digest(workers: int) -> None:
    w = World(workers=workers, reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.set_stages("3039", (*REFERRED, PASSED))
    w.clock.advance(days=4)
    w.run()
    w.gateway.sitting_votings[62] = (VOTE,)
    w.gateway.votings[(62, 16)] = (Vote(mp=1, club="KO", vote="YES"),)
    w.clock.advance(days=1)

    report = w.run()
    source = w.publication("3039", PublicationKind.STATUS_UPDATE)

    assert report.ok and report.updates == 0
    assert len(w.publisher.texts(PublicationKind.STATUS_UPDATE)) == 1
    assert source is not None and source.delivery is not None
    assert (
        source.status is PublicationStatus.QUEUED and source.delivery.destination == "digest_only"
    )
    assert (
        source.delivery.attention is not None and source.delivery.attention.novelty == "enrichment"
    )
    assert source.delivery.edit_message_id is None
    service = w.container.digest_service(dry_run=False)
    assert service is not None
    digest = service.build("2026-W37")
    assert any(entry.source_publication_ids == (source.id,) for entry in digest.updates)


def test_digest_merges_the_decision_with_late_votes_and_keeps_membership() -> None:
    w = World(reader_attention="on")
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach", stages=REFERRED)
    w.run()
    w.set_stages("3039", (*REFERRED, PASSED))
    w.clock.advance(days=4)
    w.run()
    w.gateway.sitting_votings[62] = (VOTE,)
    w.gateway.votings[(62, 16)] = (Vote(mp=1, club="KO", vote="YES"),)
    w.clock.advance(days=1)
    w.run()
    source = w.publication("3039", PublicationKind.STATUS_UPDATE)
    assert source is not None and source.id is not None
    service = w.container.digest_service(dry_run=False)
    assert service is not None

    digest = service.build("2026-W37")
    result = service.publish("2026-W37")

    assert len(digest.updates) == 1 and digest.updates[0].source_publication_ids == (source.id,)
    assert result.published
    text = "\n".join(w.publisher.texts(PublicationKind.DIGEST))
    assert text.count("Сейм принял закон") == 1
    assert "261 за" in text and "KO 1" in text
    assert w.repo.digest_owner(source.id) is not None
