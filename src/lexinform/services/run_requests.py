"""Execute saved phase requests under the workflow's state lock."""

import logging
from collections.abc import Callable

from lexinform.errors import DeliveryUncertainError, ServiceUnavailableError, StateCheckpointError
from lexinform.models import (
    DISPATCHED,
    Command,
    CommandOutcome,
    IncomingCommand,
    OutcomeStatus,
    parse_command,
)
from lexinform.ports import BillRepository, Clock, CommandInbox, OperatorReplier, StateCheckpoint

log = logging.getLogger(__name__)


class RunRequests:
    def __init__(
        self,
        repo: BillRepository,
        inbox: CommandInbox,
        replier: OperatorReplier,
        checkpoint: StateCheckpoint,
        clock: Clock,
        execute: Callable[[Command], CommandOutcome],
        *,
        workflow_url: str | None = None,
    ) -> None:
        self._repo = repo
        self._inbox = inbox
        self._replier = replier
        self._checkpoint = checkpoint
        self._clock = clock
        self._execute = execute
        self._workflow_url = workflow_url

    def drain(self) -> bool:
        failed = False
        for incoming in self._inbox.pending():
            if incoming.run_requested:
                failed |= not self._run(incoming)
        return not failed

    def _run(self, incoming: IncomingCommand) -> bool:
        identity = incoming.update_id
        saved_reply = None
        if self._repo.record_command(incoming, workflow_url=self._workflow_url):
            self._checkpoint()
            outcome = self._execute_checked(incoming)
        else:
            state = self._repo.command_state(identity)
            assert state is not None, "the repository refused an already recorded update"
            if state.handled_at is not None:
                self._checkpoint()
                self._inbox.done(incoming)
                return True
            saved_reply = state.reply
            outcome = CommandOutcome(
                status=OutcomeStatus.ERROR
                if state.executed_at is None or state.failed
                else OutcomeStatus.REPORTED,
                note=state.reply
                or "Запуск прерван, итог неизвестен. Проверьте прогон перед повтором.",
                rendered_reply=state.rendered_reply,
                request_links=(state.workflow_url,) if state.workflow_url else (),
            )
        self._repo.mark_command_executed(
            identity,
            at=self._clock.now(),
            outcome=saved_reply or outcome.line(),
            failed=not outcome.ok,
            rendered_reply=self._replier.render(incoming, outcome),
        )
        self._checkpoint()
        try:
            self._replier.reply(incoming, outcome)
        except DeliveryUncertainError:
            log.warning("Technical reply for request %d has an unknown delivery outcome", identity)
        except ServiceUnavailableError:
            return False
        except Exception:
            log.exception("Technical reply for request %d was refused", identity)
        self._repo.mark_command_handled(
            identity, at=self._clock.now(), reply=saved_reply or outcome.line()
        )
        self._checkpoint()
        self._inbox.done(incoming)
        return outcome.ok

    def _execute_checked(self, incoming: IncomingCommand) -> CommandOutcome:
        parsed = parse_command(incoming.text)
        if parsed is None or parsed.error or parsed.name not in DISPATCHED:
            return CommandOutcome(status=OutcomeStatus.ERROR, note="Некорректный запрос запуска.")
        try:
            outcome = self._execute(parsed)
        except StateCheckpointError:
            raise
        except Exception as exc:
            outcome = CommandOutcome(status=OutcomeStatus.ERROR, note=f"Ошибка запуска: {exc}")
        return outcome.model_copy(
            update={
                "request_links": (self._workflow_url,) if self._workflow_url else (),
            }
        )
