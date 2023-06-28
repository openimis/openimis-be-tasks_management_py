from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.models import User
from core.validation import BaseModelValidation
from tasks_management.models import TaskGroup, TaskExecutor, Task


class TaskValidation(BaseModelValidation):
    OBJECT_TYPE = Task


class TaskGroupValidation(BaseModelValidation):
    OBJECT_TYPE = TaskGroup

    @classmethod
    def validate_create(cls, user, **data):
        errors = validate_task_group(data)
        if errors:
            raise ValidationError(errors)
        super().validate_create(user, **data)

    @classmethod
    def validate_update(cls, user, **data):
        errors = validate_task_group(data)
        if errors:
            raise ValidationError(errors)
        super().validate_create(user, **data)


class TaskExecutorValidation(BaseModelValidation):
    OBJECT_TYPE = TaskExecutor

    @classmethod
    def validate_create(cls, user, **data):
        errors = validate_task_executor(data)
        if errors:
            raise ValidationError(errors)
        super().validate_create(user, **data)


def validate_task_group(data, uuid=None):
    return [
        *validate_not_empty_field(data.get("code"), "code"),
        *validate_bf_unique_code(data.get('code'), uuid),
    ]


def validate_task_executor(data, uuid=None):
    return [
        *validate_user_exists(data.get("user_id"))
    ]


def validate_user_exists(user_id):
    if not User.objects.filter(id=user_id).first():
        return [{"message": _("tasks_management.validation.group_executor.user_does_not_exist" % {
            'code': user_id
        })}]
    return []


def validate_bf_unique_code(code, uuid=None):
    instance = TaskGroup.objects.filter(code=code, is_deleted=False).first()
    if instance and instance.uuid != uuid:
        return [{"message": _("tasks_management.validation.task_group.code_exists" % {
            'code': code
        })}]
    return []


def validate_not_empty_field(string, field):
    if not string:
        return [{"message": _("tasks_management.validation.field_empty") % {
            'field': field
        }}]
    return []
