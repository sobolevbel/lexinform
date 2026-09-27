from argparse import ArgumentParser

from django.core.management import BaseCommand, CommandError

from lexinform.clock import SystemClock
from lexinform_web.accounts.models import User
from lexinform_web.ingestion.maintenance import resolve_issues
from lexinform_web.ingestion.models import ImportIssue


class Command(BaseCommand):
    help = "List open import issues, or resolve the given ones with an operator's reason."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("ids", nargs="*", type=int)
        parser.add_argument("--operator", metavar="EMAIL")
        parser.add_argument("--resolution", default="")

    def handle(self, *args: object, **options: object) -> None:
        raw = options["ids"]
        ids = [int(str(pk)) for pk in raw] if isinstance(raw, list) else []
        open_issues = ImportIssue.objects.filter(resolved_at__isnull=True).order_by("pk")
        if not ids:
            for issue in open_issues:
                self.stdout.write(f"{issue.pk}\t{issue.kind}\t{issue.key}\t{issue.detail}")
            return
        actor = User.objects.filter(email__iexact=str(options["operator"] or "")).first()
        if actor is None:
            raise CommandError("unknown operator")
        try:
            resolved = resolve_issues(
                list(open_issues.filter(pk__in=ids)),
                actor=actor,
                resolution=str(options["resolution"]),
                now=SystemClock().now(),
            )
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"resolved {resolved} of {len(ids)}")
