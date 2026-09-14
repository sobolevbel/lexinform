"""The command inbox as a directory of files.

The module has one branch that decides whether a run survives a bad file, and until this it had
no test at all: the only exercise it got wrote a valid command beside a `README.md`, which never
matches `glob("*.json")`.
"""

from datetime import UTC, datetime
from pathlib import Path

from lexinform.adapters.inbox_files import FileInbox, inbox_file_name
from lexinform.models import IncomingCommand


def _command(update_id: int, text: str = "/status") -> IncomingCommand:
    return IncomingCommand(
        update_id=update_id,
        chat_id="-1001",
        message_id=update_id * 2,
        text=text,
        received_at=datetime(2026, 9, 11, 8, 0, tzinfo=UTC),
    )


def _file(directory: Path, command: IncomingCommand) -> Path:
    path = directory / inbox_file_name(command)
    path.write_text(command.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_commands_are_read_in_the_order_of_their_update_id(tmp_path: Path) -> None:
    for update_id in (12, 3, 7):
        _file(tmp_path, _command(update_id))
    assert [c.update_id for c in FileInbox(tmp_path).pending()] == [3, 7, 12]


def test_a_file_that_is_not_a_command_is_kept_and_the_rest_still_run(tmp_path: Path) -> None:
    """A damaged or hand-made file must not end the phase: the commands beside it are still
    answered, and the file stays for the operator to look at."""
    _file(tmp_path, _command(5))
    (tmp_path / "6.json").write_text('{"update_id": 6, "chat_id":', encoding="utf-8")
    (tmp_path / "7.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not json at all", encoding="utf-8")

    assert [c.update_id for c in FileInbox(tmp_path).pending()] == [5]
    assert (tmp_path / "6.json").exists() and (tmp_path / "7.json").exists()


def test_a_directory_that_is_not_there_is_an_empty_inbox(tmp_path: Path) -> None:
    assert FileInbox(tmp_path / "never-checked-out").pending() == []


def test_a_command_whose_file_is_already_gone_is_still_done(tmp_path: Path) -> None:
    command = _command(9)
    _file(tmp_path, command)
    inbox = FileInbox(tmp_path)
    inbox.done(command)
    inbox.done(command)  # the workflow deleted it in between: not an error
    assert inbox.pending() == []
