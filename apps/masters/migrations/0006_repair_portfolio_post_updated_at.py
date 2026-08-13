"""Repair production schema after the already-applied 0005 was rewritten.

The first deployed version of 0005 created MasterPortfolioPost without
``updated_at``. A later commit added the field to the same migration file, but
Django does not re-run a migration whose name is already recorded. This
database-only, idempotent repair keeps both old production databases and fresh
installations valid.
"""

from django.db import migrations


def add_missing_updated_at(apps, schema_editor):
    post_model = apps.get_model("masters", "MasterPortfolioPost")
    table_name = post_model._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }
    if "updated_at" not in columns:
        schema_editor.add_field(
            post_model,
            post_model._meta.get_field("updated_at"),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("masters", "0005_masterportfoliopost_masterportfoliopostimage"),
    ]

    operations = [
        migrations.RunPython(add_missing_updated_at, migrations.RunPython.noop),
    ]
