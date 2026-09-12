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
from lexinform.models import ChannelPost, parse_command
from lexinform.ports import CommandAcknowledger, InboxWriter, UpdatesSource

log = logging.getLogger(__name__)


class CommandListener:
    def __init__(
        self,
        updates: UpdatesSource,
        writer: InboxWriter | None,
        acknowledger: CommandAcknowledger | None,
        *,
        channel_id: str,
        poll_timeout: int = 50,
        retry_delay: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`writer=None` is a dry run: commands are logged, nothing is filed or confirmed.

        `filed` collects what this process filed, or would have filed in a dry run.
        """
        self._updates = updates
        self._writer = writer
        self._ack = acknowledger
        self._channel = channel_id
        self._poll_timeout = poll_timeout
        self._retry_delay = retry_delay
        self._sleep = sleep
        self._offset: int | None = None
        self.filed: list[ChannelPost] = []

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
        for post in posts:
            if self._is_command(post):
                if not self._file(post):
                    break
                filed += 1
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
                log.warning("%s; retrying in %.0fs", exc.describe(), self._retry_delay)
                self._sleep(self._retry_delay)
            except Exception as exc:
                log.exception("listener error: %s; retrying in %.0fs", exc, self._retry_delay)
                self._sleep(self._retry_delay)

    def _is_command(self, post: ChannelPost) -> bool:
        if not post.is_from(self._channel):
            log.debug("update %d: not from the technical channel, ignored", post.update_id)
            return False
        return post.text is not None and parse_command(post.text) is not None

    def _file(self, post: ChannelPost) -> bool:
        """Put one command in the inbox; True when it is safely there and the offset may move.

        The acknowledgement in the channel is a courtesy: the command is filed either way.
        """
        command = post.as_command()
        if self._writer is None:
            log.info("dry run: would file update %d: %s", post.update_id, post.text)
            self.filed.append(post)
            return True
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
                self._ack.queued(command)
            except Exception as exc:
                log.warning("could not acknowledge update %d: %s", post.update_id, exc)
        return True
