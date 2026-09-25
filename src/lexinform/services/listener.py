"""The relay between the technical channel and the runs: `lexinform listen`.

Runs where something is always on (a small VPS): long-polls Telegram for posts in the
technical channel, files every command into the inbox (the git branch `inbox`; the writer then
asks GitHub to run the workflow), acknowledges it under the post and confirms the update.
Nothing else: no database, no model, no Sejm. An update is confirmed only once its command is
filed, so a GitHub outage leaves it with Telegram (which keeps unconfirmed updates for 24 hours).
"""

import logging
import time
from collections.abc import Callable

from lexinform.errors import ServiceUnavailableError
from lexinform.models import DISPATCHED, ChannelPost, Command, IncomingCommand, parse_command
from lexinform.ports import CommandAcknowledger, InboxWriter, UpdatesSource, WorkflowStarter

log = logging.getLogger(__name__)


class CommandListener:
    def __init__(
        self,
        updates: UpdatesSource,
        writer: InboxWriter | None,
        acknowledger: CommandAcknowledger | None,
        *,
        starter: WorkflowStarter | None = None,
        channel_id: str,
        poll_timeout: int = 50,
        retry_delay: float = 15.0,
        max_retry_delay: float = 600.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`writer=None` is a dry run: commands are logged, nothing is filed or confirmed.

        `filed` collects what this process filed, or would have filed in a dry run.
        """
        self._updates = updates
        self._writer = writer
        self._starter = starter
        self._ack = acknowledger
        self._channel = channel_id
        self._poll_timeout = poll_timeout
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._sleep = sleep
        self._offset: int | None = None
        self._stalled = False
        self._failures = 0
        self.filed: list[ChannelPost] = []
        self._acknowledged: dict[int, IncomingCommand] = {}

    @property
    def offset(self) -> int | None:
        """The next `getUpdates` offset: everything below it is handled and may be confirmed."""
        return self._offset

    def poll_once(self, *, timeout: int | None = None) -> int:
        """One `getUpdates` and the handling of what it brought; returns how many commands
        were filed. Stops at the first command that could not be filed (the offset stays
        below it, so Telegram delivers it again)."""
        wait = self._poll_timeout if timeout is None else timeout
        posts = self._updates.get_updates(offset=self._offset, timeout=wait)
        filed = 0
        self._stalled = False
        for post in posts:
            command = self._command_in(post)
            if command is not None:
                handled = (
                    self._start_run(post, command)
                    if command.name in DISPATCHED
                    else self._file(post)
                )
                if not handled:
                    self._stalled = True
                    break
                filed += 1
                self._acknowledged.pop(post.update_id, None)
            self._offset = post.update_id + 1
        return filed

    def confirm(self) -> None:
        """Tell Telegram the handled updates are done with (an empty poll at the offset)."""
        if self._offset is not None and self._writer is not None:
            self._updates.get_updates(offset=self._offset, timeout=0)

    def run_forever(self, *, stop: Callable[[], bool] = lambda: False) -> None:
        log.info("listening for commands in %s", self._channel)
        while not stop():
            try:
                self.poll_once()
            except ServiceUnavailableError as exc:
                self._back_off(exc.describe())
            except Exception as exc:
                log.exception("listener error: %s", exc)
                self._back_off(str(exc))
            else:
                if self._stalled:
                    self._back_off("a command could not be filed")
                else:
                    self._failures = 0

    def _back_off(self, reason: str) -> None:
        """Wait before the next poll, longer the longer the failures have been going on.

        A poll that ends in a command it could not file returns without raising, so without
        this the loop would spin at network speed: Telegram answers an update it has already
        delivered at once, and the offset cannot move until the command is in the inbox. That
        costs nothing while the inbox is merely unreachable (the writer retries with sleeps of
        its own), but a refusal that no retry fixes — a revoked token, a repository the PAT can
        no longer write — raises immediately, and a relay pinned to a 384 MB VPS would fill its
        log until somebody noticed.
        """
        self._failures += 1
        # A relay nobody rescues doubles for days; 2**1024 does not fit in a float.
        growth = 2 ** min(self._failures - 1, 32)
        delay = min(self._retry_delay * growth, self._max_retry_delay)
        log.warning("%s; retrying in %.0fs", reason, delay)
        self._sleep(delay)

    def _command_in(self, post: ChannelPost) -> Command | None:
        if not post.is_from(self._channel):
            log.debug("update %d: not from the technical channel, ignored", post.update_id)
            return None
        return parse_command(post.text) if post.text is not None else None

    def _start_run(self, post: ChannelPost, command: Command) -> bool:
        """A command that *is* a run is not filed: `/run`, and the four phases a run is made of.

        The relay asks GitHub to start the workflow with the inputs the operator named, and says
        so under the command. True when the offset may move: a misspelled option is answered and
        passed over — leaving it unhandled would make Telegram deliver it again for ever — while
        an outage is not, the command being worth trying again with nothing started.
        """
        acted = post.as_command()
        if command.error is not None:
            log.info("update %d: %s", post.update_id, command.error)
            self._say(lambda ack: ack.started(acted, command.error or ""))
            self.filed.append(post)
            return True
        if self._starter is None:
            log.info("dry run: would start the workflow with %s", command.inputs or "no inputs")
            self.filed.append(post)
            return True
        acted = self._acknowledge(post)
        inputs = dict(command.inputs)
        if acted.acknowledgement_id is not None:
            inputs["log_message_id"] = str(acted.acknowledgement_id)
        named = ", ".join(f"{k}={v}" for k, v in command.inputs.items()) or "no inputs"
        where = self._starter.workflow_url
        self._say(lambda ack: ack.started(acted, f"{command.name} started ({named})", url=where))
        try:
            self._starter.start_run(inputs)
        except ServiceUnavailableError as exc:
            log.warning("update %d: the run was not started: %s", post.update_id, exc.describe())
            self._say(lambda ack: ack.started(acted, "dispatch unavailable; relay will retry"))
            return False
        except Exception as exc:
            log.exception("update %d: the run was not started: %s", post.update_id, exc)
            refused = f"the run was not started: {exc}"
            self._say(lambda ack: ack.started(acted, refused))
            self.filed.append(post)
            return True
        self.filed.append(post)
        log.info("update %d started the workflow (%s)", post.update_id, named)
        return True

    def _say(self, tell: Callable[[CommandAcknowledger], None]) -> None:
        """The acknowledgement is a courtesy: the run is started either way."""
        if self._ack is None:
            return
        try:
            tell(self._ack)
        except Exception as exc:  # noqa: BLE001 — a channel that is down changes nothing here
            log.warning("could not answer in the channel: %s", exc)

    def _file(self, post: ChannelPost) -> bool:
        """Put one command in the inbox; True when it is safely there and the offset may move.

        The acknowledgement in the channel is a courtesy: the command is filed either way.
        """
        command = post.as_command()
        if self._writer is None:
            log.info("dry run: would file update %d: %s", post.update_id, post.text)
            self.filed.append(post)
            return True
        command = self._acknowledge(post)
        try:
            self._writer.put(command)
        except ServiceUnavailableError as exc:
            log.warning("update %d not filed: %s", post.update_id, exc.describe())
            return False
        except Exception as exc:
            log.exception("update %d not filed: %s", post.update_id, exc)
            return False
        self.filed.append(post)
        log.info("filed update %d: %s", post.update_id, post.text)
        if self._ack is not None:
            try:
                if post.callback_id is not None:
                    # A press has no message of its own to reply under, only the draft it hangs
                    # on, and the button spins until Telegram is told it arrived.
                    self._ack.pressed(post.callback_id)
            except Exception as exc:
                log.warning("could not acknowledge update %d: %s", post.update_id, exc)
        return True

    def _acknowledge(self, post: ChannelPost) -> IncomingCommand:
        command = self._acknowledged.get(post.update_id, post.as_command())
        if (
            self._ack is not None
            and post.callback_id is None
            and command.acknowledgement_id is None
        ):
            try:
                message_id = self._ack.queued(command)
                command = command.model_copy(update={"acknowledgement_id": message_id})
                self._acknowledged[post.update_id] = command
            except Exception as exc:
                log.warning("could not acknowledge update %d: %s", post.update_id, exc)
        return command
