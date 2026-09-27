import json
from argparse import ArgumentParser
from pathlib import Path

from django.conf import settings
from django.core.management import BaseCommand, CommandError

from lexinform.clock import SystemClock
from lexinform_web.accounts.models import User
from lexinform_web.ingestion.acquisition import GitSnapshotSource
from lexinform_web.ingestion.maintenance import run_import


class Command(BaseCommand):
    help = "Import the bot's state snapshot once and print the run report as JSON."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--accept-drop", action="store_true")
        parser.add_argument("--rebaseline", metavar="COMMIT")
        parser.add_argument("--operator", metavar="EMAIL")
        parser.add_argument("--reason", default="")

    def handle(self, *args: object, **options: object) -> None:
        remote = str(settings.STATE_REMOTE)
        if not remote:
            raise CommandError("LEXINFORM_WEB_STATE_REMOTE is not set")
        actor = None
        if options["operator"]:
            actor = User.objects.filter(email__iexact=str(options["operator"])).first()
            if actor is None:
                raise CommandError("unknown operator")
        source = GitSnapshotSource(
            Path(remote) if Path(remote).is_absolute() else remote, Path(settings.STATE_CACHE)
        )
        rebaseline = options["rebaseline"]
        try:
            report = run_import(
                source,
                clock=SystemClock(),
                public_channel=settings.STATE_PUBLIC_CHANNEL,
                accept_drop=bool(options["accept_drop"]),
                rebaseline=str(rebaseline) if rebaseline else None,
                actor=actor,
                reason=str(options["reason"]),
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(report.__dict__, default=str, sort_keys=True))
        if report.status == "failed":
            raise CommandError(f"import failed: {report.error_kind}")
