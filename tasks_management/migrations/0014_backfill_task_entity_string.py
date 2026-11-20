import logging

from django.db import DatabaseError, migrations, transaction

logger = logging.getLogger(__name__)


def backfill_entity_string(apps, schema_editor):
    """
    Fill Task.entity_string (and the history rows) for the tasks that exist
    before OP-3332, in batches.

    The entities are loaded through their current model classes, since the text
    is what their `__str__` returns today. If an entity module's own migrations
    are still pending, that model may not match its table yet: the backfill is
    then skipped with a warning instead of blocking the migration, and
    `manage.py refresh_task_entity_string` completes it afterwards.
    """
    from tasks_management.models import refresh_entity_strings

    for name in ("Task", "HistoricalTask"):
        model = apps.get_model("tasks_management", name)
        try:
            with transaction.atomic():
                changed = refresh_entity_strings(model.objects.all())
            logger.info("tasks_management: entity_string backfilled on %s %s rows", changed, name)
        except DatabaseError:
            logger.warning(
                "tasks_management: entity_string backfill of %s skipped, run "
                "'manage.py refresh_task_entity_string' once all migrations are applied",
                name, exc_info=True,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("tasks_management", "0013_task_entity_string"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(backfill_entity_string, migrations.RunPython.noop),
    ]
