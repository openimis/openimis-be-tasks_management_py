from django.db import migrations

from core.models import Role, RoleRight

tasks_rights = [191001, 191002, 191003, 191004]
imis_administrator_system = 64
task_triage = 2097152


def add_rights(role_id):
    role = Role.objects.get(is_system=role_id)
    for right_id in tasks_rights:
        if not RoleRight.objects.filter(validity_to__isnull=True, role=role, right_id=right_id).exists():
            _add_right_for_role(role, right_id)


def _add_right_for_role(role, right_id):
    RoleRight.objects.create(role=role, right_id=right_id, audit_user_id=1)


def remove_rights(role_id):
    RoleRight.objects.filter(
        role__is_system=role_id,
        right_id__in=tasks_rights,
        validity_to__isnull=True
    ).delete()


def on_migration(apps, schema_editor):
    add_rights(imis_administrator_system)
    add_rights(task_triage)


def on_reverse_migration(apps, schema_editor):
    remove_rights(imis_administrator_system)
    remove_rights(task_triage)


class Migration(migrations.Migration):
    dependencies = [
        ('tasks_management', '0004_auto_20230628_1404')
    ]

    operations = [
        migrations.RunPython(on_migration, on_reverse_migration),
    ]
