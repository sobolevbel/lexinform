from argparse import ArgumentParser
from uuid import uuid4

from django.core.management import BaseCommand, call_command

from lexinform_web.operations.worker import worker_lock


class Command(BaseCommand):
    help = "Run the single database worker under the shared recovery lock."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--batch", action="store_true")

    def handle(self, *args: object, **options: object) -> None:
        with worker_lock():
            call_command(
                "db_worker",
                reload=False,
                batch=bool(options["batch"]),
                interval=0.1,
                startup_delay=False,
                worker_id=str(uuid4()),
            )
