import datetime as dt

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from lexinform.models import PublicationKind, PublicationStatus, RunMode
from tests.fakes import make_analysis
from tests.harness import World


class DeliveryRecoveryMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.world = World(reader_attention="on")
        self.world.add_bill("3039", "Projekt ustawy o cudzoziemcach")
        analysis = make_analysis()
        analysis.practical_impact = "Старые заявления не подавать заново; 200 PLN. " * 220
        self.world.llm.script["3039"] = analysis
        self.world.publisher.unknown_continuation_attempt = 1
        self.world.run()
        root = self.world.publication("3039")
        assert root is not None and root.id is not None and root.delivery is not None
        self.root_id = root.id
        self.parts = root.delivery.continuation_ids
        assert len(self.parts) >= 2
        self.texts: dict[int, str] = {}
        for identity in (self.root_id, *self.parts):
            saved = self.world.repo.publication_by_id(identity)
            assert saved is not None and saved.delivery is not None
            self.texts[identity] = saved.delivery.rendered_text
        self.terminal: PublicationStatus | None = None

    @rule()
    def restart(self) -> None:
        dump = self.world.repo.dump()
        now = self.world.clock.now()
        self.world.repo.close()
        self.world = World(reader_attention="on")
        self.world.clock.current = now
        self.world.repo.restore(dump)

    @rule()
    def drain(self) -> None:
        cards = len(self.world.publisher.texts(PublicationKind.NEW_BILL))
        self.world.container.delivery_service(dry_run=False).run()
        assert len(self.world.publisher.texts(PublicationKind.NEW_BILL)) == cards

    @rule()
    def retain(self) -> None:
        self.world.repo.prune_history(
            before=self.world.clock.now() + dt.timedelta(days=1),
            memo_before=self.world.clock.now(),
        )

    @rule()
    def next_day(self) -> None:
        self.world.clock.advance(days=1)

    @precondition(lambda self: self.terminal is None)
    @rule(action=st.sampled_from(["confirm 888", "retry", "dismiss checked absence"]))
    def resolve(self, action: str) -> None:
        self.world.command(f"/delivery {self.parts[0]} {action}")
        self.world.run(
            mode=RunMode.COMMANDS,
            discover=False,
            analyze=False,
            track=False,
            max_publish=0,
        )
        self.world.publisher.unknown_continuation_attempt = None
        self.world.container.delivery_service(dry_run=False).run()
        self.terminal = (
            PublicationStatus.DISMISSED if action.startswith("dismiss") else PublicationStatus.SENT
        )

    @invariant()
    def frozen_payload_and_unknown_outcome_survive_every_transition(self) -> None:
        root = self.world.repo.publication_by_id(self.root_id)
        assert root is not None and root.status is PublicationStatus.SENT
        if self.terminal is None:
            assert root.delivery is not None
        for index, identity in enumerate(self.parts):
            saved = self.world.repo.publication_by_id(identity)
            assert saved is not None
            expected = self.terminal or (
                PublicationStatus.UNKNOWN if index == 0 else PublicationStatus.QUEUED
            )
            assert saved.status is expected
        for identity, text in self.texts.items():
            saved = self.world.repo.publication_by_id(identity)
            assert saved is not None
            if saved.delivery is not None:
                assert saved.delivery.rendered_text == text

    def teardown(self) -> None:
        self.world.repo.close()


TestDeliveryRecovery = DeliveryRecoveryMachine.TestCase
TestDeliveryRecovery.settings = settings(max_examples=25, stateful_step_count=15, deadline=None)
