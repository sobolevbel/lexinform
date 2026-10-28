from lexinform.models import PublicationKind
from tests.harness import World


def test_reply_without_mnemonic_uses_saved_context() -> None:
    w = World()
    w.add_bill("3039", "Projekt ustawy o cudzoziemcach")
    w.run()
    w.set_stages("3039", ())
    bill = w.bill("3039")
    assert bill.analysis is not None
    w.publish_act("3039")
    w.run()

    notices = w.publisher.texts(PublicationKind.ACT_PUBLISHED) + w.publisher.texts(
        PublicationKind.STATUS_UPDATE
    )
    assert notices and bill.analysis.analysis.summary.split(". ")[0] in notices[-1]
