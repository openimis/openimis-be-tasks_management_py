from django.db import migrations

from core.models import Role, RoleRight

users_search_right = 121701
task_triage = 2097152


def add_rights(apps, schema_editor):
    role = Role.objects.get(is_system=task_triage)
    if not RoleRight.objects.filter(validity_to__isnull=True, role=role, right_id=users_search_right).exists():
        _add_right_for_role(role, users_search_right)


def _add_right_for_role(role, right_id):
    RoleRight.objects.create(role=role, right_id=right_id, audit_user_id=1)


def remove_rights(apps, schema_editor):
    RoleRight.objects.filter(
        role__is_system=task_triage,
        right_id=users_search_right,
        validity_to__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('tasks_management', '0005_add_task_perms_to_admin')
    ]

    operations = [
        migrations.RunPython(add_rights, remove_rights),
    ]
