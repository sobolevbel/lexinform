from django.core.exceptions import ImproperlyConfigured
from django.core.management import BaseCommand, CommandError
from wagtail.models import Site

from lexinform_web.editorial.demo import seed_demo_guide


class Command(BaseCommand):
    help = "Create an unpublished local reading fixture without replacing editorial changes."

    def handle(self, *args: object, **options: object) -> None:
        try:
            page, created = seed_demo_guide()
        except (ImproperlyConfigured, ValueError, Site.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        state = "Created draft" if created else "Kept existing content"
        self.stdout.write(f"{state}: /admin/pages/{page.pk}/edit/")
