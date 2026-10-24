"""Exercise fatal reader checkpoints through real CLI commands over offline fakes."""

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

from lexinform import cli
from lexinform.adapters.batch_checkpoint import GitBatchCheckpoint
from lexinform.errors import StateCheckpointError
from lexinform.models import Publication, PublicationStatus
from lexinform.ports import PublishResult
from tests.harness import World
from tests.scenario.test_card_remote_checkpoints import connect, git
from tests.scenario.test_update_remote_checkpoints import queued, row


def run(command: str, boundary: int, output: Path) -> None:
    case = queued("off", with_act=False)
    world = case.world
    remote = connect(world, output.parent)
    original_checkpoint = GitBatchCheckpoint.__call__
    original_publish = world.publisher.publish_prepared
    effects: list[int] = []
    posts, edits = len(world.publisher.sent), len(world.publisher.edited)
    reached: list[int] = []

    def checkpoint(writer: GitBatchCheckpoint) -> None:
        current = row(world, case.identity)
        expected = PublicationStatus.PENDING if boundary == 1 else PublicationStatus.SENT
        if current.status is expected and len(effects) == boundary - 1:
            reached.append(boundary)
            raise StateCheckpointError("injected CLI reader checkpoint failure")
        original_checkpoint(writer)

    def publish(publication: Publication) -> PublishResult:
        assert publication.id is not None, "the CLI reader receives saved physical identities"
        effects.append(publication.id)
        return original_publish(publication)

    try:
        with (
            patch.object(cli, "_container", return_value=world.container),
            patch.object(GitBatchCheckpoint, "__call__", checkpoint),
            patch.object(world.publisher, "publish_prepared", publish),
            patch.object(sys, "argv", ["lexinform", command]),
        ):
            cli.main()
    finally:
        durable = World()
        durable.repo.restore(git(remote, "show", "state:state.sql"))
        pending = row(durable, case.identity)
        output.write_text(
            json.dumps(
                {
                    "boundary": reached,
                    "effects": effects,
                    "target": case.identity,
                    "reader_posts": len(world.publisher.sent) - posts,
                    "reader_edits": len(world.publisher.edited) - edits,
                    "remote_status": pending.status.value,
                    "remote_has_html": bool(pending.delivery and pending.delivery.rendered_text),
                }
            )
            + "\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("collect-batches", "drain-deliveries"))
    parser.add_argument("boundary", type=int, choices=(1, 2))
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    with (
        patch(
            "socket.socket.connect", side_effect=AssertionError("External sockets are forbidden")
        ),
        patch(
            "socket.create_connection", side_effect=AssertionError("External sockets are forbidden")
        ),
    ):
        run(arguments.command, arguments.boundary, arguments.output)


if __name__ == "__main__":
    main()
