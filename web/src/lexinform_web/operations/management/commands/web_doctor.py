from django import VERSION as DJANGO_VERSION
from django.core.management import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from wagtail import VERSION as WAGTAIL_VERSION


class Command(BaseCommand):
    help = "Check framework versions, PostgreSQL connectivity and pending migrations."

    def handle(self, *args: object, **options: object) -> None:
        if DJANGO_VERSION[:2] != (6, 1):
            raise CommandError(f"expected Django 6.1, found {'.'.join(map(str, DJANGO_VERSION))}")
        if WAGTAIL_VERSION[:2] != (8, 0):
            raise CommandError(f"expected Wagtail 8.0, found {'.'.join(map(str, WAGTAIL_VERSION))}")

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::integer")
            postgres_version = int(cursor.fetchone()[0])
        if postgres_version // 10000 != 17:
            raise CommandError(
                f"expected PostgreSQL 17, found server_version_num={postgres_version}"
            )

        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            names = ", ".join(f"{migration.app_label}.{migration.name}" for migration, _ in pending)
            raise CommandError(f"pending migrations: {names}")

        self.stdout.write(
            self.style.SUCCESS(
                "web stack ready: "
                f"Django {'.'.join(map(str, DJANGO_VERSION[:3]))}, "
                f"Wagtail {'.'.join(map(str, WAGTAIL_VERSION[:3]))}, PostgreSQL 17"
            )
        )
