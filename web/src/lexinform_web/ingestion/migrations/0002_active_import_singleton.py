from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def create_pointer(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    apps.get_model("ingestion", "ActiveImport").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0001_initial")]

    operations = [migrations.RunPython(create_pointer, migrations.RunPython.noop)]
