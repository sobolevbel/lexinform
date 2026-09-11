"""The inbox as a directory of JSON files, one per Telegram update (`{update_id}.json`).

In production the directory is a worktree of the git branch `inbox`: the relay on the VPS adds
a file per command through the GitHub Contents API, the workflow commits the deletions after
the run. Locally any directory works (`LEXINFORM_INBOX_DIR`).
"""

import logging
from pathlib import Path

from pydantic import ValidationError

from lexinform.models import IncomingCommand

log = logging.getLogger(__name__)


class FileInbox:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def pending(self) -> list[IncomingCommand]:
        if not self._dir.is_dir():
            return []
        commands: list[IncomingCommand] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                commands.append(IncomingCommand.model_validate_json(path.read_bytes()))
            except (OSError, ValueError, ValidationError) as exc:
                # Not a command file (a hand-made file, a damaged one): left for the operator.
                log.warning("inbox: %s is not a command: %s", path.name, exc)
        commands.sort(key=lambda c: c.update_id)
        return commands

    def done(self, command: IncomingCommand) -> None:
        (self._dir / inbox_file_name(command)).unlink(missing_ok=True)


def inbox_file_name(command: IncomingCommand) -> str:
    """The relay and the inbox agree on the file name: the update id."""
    return f"{command.update_id}.json"
