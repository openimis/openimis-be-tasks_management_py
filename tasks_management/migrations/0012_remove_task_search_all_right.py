from django.db import migrations
from core.utils import insert_role_right_for_system, remove_role_right_for_system

# 191005 (task "search all") is retired: it was never enforced server-side and
# duplicated access the IMIS Administrator / Task Triage roles already have via
# their task-group rights (is_task_triage) and 191001. Mirrors 0010 in reverse.
tasks_rights = 191005
imis_administrator_system = 64
task_triage = 2097152


def on_migration(apps, schema_editor):
    remove_role_right_for_system(imis_administrator_system, tasks_rights, apps)
    remove_role_right_for_system(task_triage, tasks_rights, apps)


def on_reverse_migration(apps, schema_editor):
    insert_role_right_for_system(imis_administrator_system, tasks_rights, apps)
    insert_role_right_for_system(task_triage, tasks_rights, apps)


class Migration(migrations.Migration):
    dependencies = [
        ('tasks_management', '0011_layered_task_flows'),
    ]

    operations = [
        migrations.RunPython(on_migration, on_reverse_migration),
    ]
