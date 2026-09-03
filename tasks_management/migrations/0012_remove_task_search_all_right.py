from django.db import migrations
from core.utils import insert_role_right_for_system, remove_role_right_for_system

# Retire 191005 (task "search all"): never enforced server-side, redundant with
# is_task_triage + 191001 for the roles that hold it. Mirrors 0010 reversed.
# Deploy the 191001-gated frontend (openimis-fe-tasks_management_js#70) first;
# both roles keep 191001 (migration 0005), so task-page access is unaffected.
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
