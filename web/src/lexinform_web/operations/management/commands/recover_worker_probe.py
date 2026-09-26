from argparse import ArgumentParser
from uuid import UUID

from django.core.management import BaseCommand, CommandError
from django_tasks_db.models import DBTaskResult

from lexinform_web.operations.worker import recover_probe


class Command(BaseCommand):
    help = "Explicitly retry a stopped worker's idempotent probe, recording the decision."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("task_id", type=UUID)
        parser.add_argument("--worker-id", required=True)
        parser.add_argument("--reason", required=True)

    def handle(self, *args: object, **options: object) -> None:
        try:
            recover_probe(
                UUID(str(options["task_id"])), str(options["worker_id"]), str(options["reason"])
            )
        except DBTaskResult.DoesNotExist as exc:
            raise CommandError("task not found") from exc
        self.stdout.write("Probe queued for retry; recovery recorded.")
