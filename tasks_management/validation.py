from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType

from core.models import User
from core.validation import BaseModelValidation, UniqueCodeValidationMixin, ObjectExistsValidationMixin, \
    StringFieldValidationMixin
from tasks_management.apps import TasksManagementConfig
from tasks_management.models import TaskGroup, TaskExecutor, Task, TaskDecision, TaskFlow, TaskFlowStep


class TaskGroupValidation(BaseModelValidation, UniqueCodeValidationMixin, ObjectExistsValidationMixin,
                          StringFieldValidationMixin):
    OBJECT_TYPE = TaskGroup

    @classmethod
    def validate_create(cls, user, **data):
        super().validate_create(user, **data)
        cls.validate_unique_code_name(data.get('code'))
        errors = validate_task_group(data)
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_update(cls, user, **data):
        super().validate_update(user, **data)
        uuid = data.get('id')
        cls.validate_object_exists(uuid)
        existing = cls.OBJECT_TYPE.objects.filter(id=uuid).first()

        incoming_code = data.get('code')
        if incoming_code != existing.code:
            cls.validate_unique_code_name(data.get('code'))
        errors = validate_task_group(data, uuid)
        if errors:
            raise ValidationError(errors)


class TaskExecutorValidation(BaseModelValidation):
    OBJECT_TYPE = TaskExecutor

    @classmethod
    def validate_create(cls, user, **data):
        super().validate_create(user, **data)
        errors = validate_task_executor(data)
        if errors:
            raise ValidationError(errors)


class TaskValidation(BaseModelValidation, ObjectExistsValidationMixin):
    OBJECT_TYPE = Task

    @classmethod
    def validate_create(cls, user, **data):
        super().validate_create(user, **data)
        errors = validate_existing_task(data)
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_update(cls, user, **data):
        super().validate_update(user, **data)
        uuid = data.get('id')
        cls.validate_object_exists(uuid)
        errors = validate_task_status(uuid)
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_delete(cls, user, **data):
        super().validate_delete(user, **data)


def validate_task_group(data, uuid=None):
    return [
        *validate_not_empty_field(data.get("code"), "code"),
        *validate_unique_task_source(data.get("task_sources"), uuid)
    ]


def validate_task_executor(data, uuid=None):
    return [
        *validate_user_exists(data.get("user_id"))
    ]


def validate_task_status(uuid):
    instance = Task.objects.get(id=uuid)
    instance_status = instance.status
    if instance_status in [Task.Status.COMPLETED, Task.Status.FAILED]:
        return [
            {"message": _("tasks_management.validation.task.updating_completed_task") % {'status': instance_status}}]
    return []


def validate_user_exists(user_id):
    if not User.objects.filter(id=user_id).exists():
        return [{"message": _("tasks_management.validation.group_executor.user_does_not_exist") % {'code': user_id}}]
    return []


def validate_not_empty_field(string, field):
    try:
        TaskGroupValidation().validate_empty_string(string)
        TaskGroupValidation().validate_string_whitespace_end(string)
        TaskGroupValidation().validate_string_whitespace_start(string)
        return []
    except ValidationError as e:
        return [{"message": _("tasks_management.validation.field_empty") % {'field': field}}]


def validate_existing_task(data):
    content_type = data.get('entity_type')
    entity_id = data.get('entity_id')

    if isinstance(content_type, ContentType):
        try:
            entity_instance = content_type.get_object_for_this_type(id=entity_id)
        except content_type.model_class().DoesNotExist:
            return [{"message": _("tasks_management.validation.entity_not_found") % {'entity_id': entity_id}}]

        filtered_tasks = Task.objects.filter(
            Q(entity_type=content_type) &
            Q(entity_id=str(entity_id)) &
            (Q(status=Task.Status.ACCEPTED) | Q(status=Task.Status.RECEIVED))
        )

        if filtered_tasks.exists():
            return [
                {"message": _("tasks_management.validation.another_task_pending") % {'instance': str(entity_instance)}}]
    return []


def validate_unique_task_source(task_sources, group_id=None, flow_id=None):
    """
    A source may bind to at most one of (TaskGroup, TaskFlow head version) so
    task assignment stays deterministic. group_id/flow_id exclude the entity
    being edited (or, for flows, the version being replaced).
    """
    task_groups_by_source = {}

    queryset = TaskGroup.objects.filter(is_deleted=False)
    if group_id:
        queryset = queryset.exclude(id=group_id)
    # Head versions only: superseded flow versions keep their json_ext but no
    # longer participate in binding (see TaskService.create).
    flow_queryset = TaskFlow.objects.filter(is_deleted=False, replacement_uuid__isnull=True)
    if flow_id:
        flow_queryset = flow_queryset.exclude(id=flow_id)

    for task_source in task_sources:
        instance = queryset.filter(json_ext__contains={"task_sources": [task_source]}).first()
        if not instance:
            instance = flow_queryset.filter(json_ext__contains={"task_sources": [task_source]}).first()
        if instance:
            task_groups_by_source[task_source] = instance.code

    if task_groups_by_source:
        return [{"message": _("tasks_management.validation.validate_unique_task_source") % {
            'task_groups_by_source': task_groups_by_source}}]
    return []


class TaskFlowValidation(BaseModelValidation, ObjectExistsValidationMixin):
    OBJECT_TYPE = TaskFlow

    @classmethod
    def validate_create(cls, user, **data):
        errors = validate_task_flow(data)
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_update(cls, user, **data):
        cls.validate_object_exists(data.get('id'))
        errors = validate_task_flow(data, flow_id=data.get('id'))
        if errors:
            raise ValidationError(errors)

    @classmethod
    def validate_replace(cls, user, **data):
        cls.validate_object_exists(data.get('id'))
        errors = validate_task_flow(data, flow_id=data.get('id'))
        if errors:
            raise ValidationError(errors)


def validate_task_flow(data, flow_id=None):
    errors = [*validate_not_empty_field(data.get('code'), 'code')]

    code = data.get('code')
    if code:
        head = TaskFlow.objects.filter(
            code=code, is_deleted=False, replacement_uuid__isnull=True,
        )
        if flow_id:
            head = head.exclude(id=flow_id)
        if head.exists():
            errors.append({"message": _(
                "tasks_management.validation.task_flow.code_exists") % {'code': code}})

    task_sources = data.get('task_sources') or []
    ineligible = [s for s in task_sources
                  if s in (TasksManagementConfig.flow_ineligible_sources or [])]
    if ineligible:
        errors.append({"message": _(
            "tasks_management.validation.task_flow.ineligible_sources"
        ) % {'sources': ", ".join(ineligible)}})
    if task_sources:
        errors.extend(validate_unique_task_source(task_sources, flow_id=flow_id))

    steps = data.get('steps')
    if steps is not None:
        errors.extend(validate_task_flow_steps(steps))
    return errors


def validate_task_flow_steps(steps):
    errors = []
    if not steps:
        errors.append({"message": _("tasks_management.validation.task_flow.no_steps")})
        return errors
    for position, step in enumerate(steps, start=1):
        group = TaskGroup.objects.filter(
            id=step.get('task_group_id'), is_deleted=False,
        ).first()
        if not group:
            errors.append({"message": _(
                "tasks_management.validation.task_flow.step_group_missing") % {'order': position}})
            continue
        pool_size = group.taskexecutor_set.filter(is_deleted=False).count()
        if pool_size == 0:
            errors.append({"message": _(
                "tasks_management.validation.task_flow.step_pool_empty"
            ) % {'order': position, 'group': group.code}})
        policy = step.get('completion_policy')
        threshold = step.get('threshold')
        valid_policies = [None, *TaskFlowStep.StepCompletionPolicy.values]
        if policy not in valid_policies:
            errors.append({"message": _(
                "tasks_management.validation.task_flow.step_policy_invalid"
            ) % {'order': position, 'policy': policy}})
            continue
        effective_policy = policy or group.completion_policy
        effective_threshold = threshold if policy else group.threshold
        if policy == TaskFlowStep.StepCompletionPolicy.N and not threshold:
            errors.append({"message": _(
                "tasks_management.validation.task_flow.step_threshold_required") % {'order': position}})
        if policy != TaskFlowStep.StepCompletionPolicy.N and threshold is not None:
            errors.append({"message": _(
                "tasks_management.validation.task_flow.step_threshold_forbidden") % {'order': position}})
        if (effective_policy == TaskFlowStep.StepCompletionPolicy.N
                and effective_threshold and pool_size and effective_threshold > pool_size):
            errors.append({"message": _(
                "tasks_management.validation.task_flow.step_threshold_exceeds_pool"
            ) % {'order': position, 'threshold': effective_threshold, 'pool': pool_size}})
    return errors


def validate_task_flow_assignment(task, flow, detach=False):
    """
    Assignment is one decision with two shapes - an ordered approval flow or a
    flat task group - so attaching, switching and detaching are validated in
    one place.

    A task carrying decisions is off limits either way: its votes are recorded
    against steps of the flow it was on, and re-pointing it would leave them
    referring to a step the task no longer travels through.
    """
    if not task:
        return [{"message": _("tasks_management.validation.task_flow_assignment.unknown_task")}]

    errors = []
    if task.status in (Task.Status.COMPLETED, Task.Status.FAILED):
        errors.append({"message": _(
            "tasks_management.validation.task_flow_assignment.task_closed"
        ) % {'status': task.status}})
    if TaskDecision.objects.filter(task=task, is_deleted=False).exists():
        errors.append({"message": _(
            "tasks_management.validation.task_flow_assignment.decisions_exist")})

    if detach:
        if not task.flow_id:
            errors.append({"message": _(
                "tasks_management.validation.task_flow_assignment.not_on_a_flow")})
        return errors

    # The same two gates create() applies when matching a flow by source.
    # A source whose business logic runs on the first vote would bypass every
    # later step, and a task resolved through a custom executor event never
    # reaches the advancement signal at all - it would sit on step 1 forever.
    if task.source in (TasksManagementConfig.flow_ineligible_sources or []):
        errors.append({"message": _(
            "tasks_management.validation.task_flow_assignment.source_ineligible"
        ) % {'source': task.source}})
    if task.executor_action_event != TasksManagementConfig.default_executor_event:
        errors.append({"message": _(
            "tasks_management.validation.task_flow_assignment.custom_executor_event"
        ) % {'event': task.executor_action_event}})

    if not flow:
        errors.append({"message": _(
            "tasks_management.validation.task_flow_assignment.unknown_flow")})
        return errors
    # Superseded versions keep serving the tasks already pinned to them but
    # must never take on a new one.
    if flow.is_deleted or flow.replacement_uuid:
        errors.append({"message": _(
            "tasks_management.validation.task_flow_assignment.flow_not_current"
        ) % {'code': flow.code}})
    elif not flow.steps.filter(is_deleted=False).exists():
        errors.append({"message": _(
            "tasks_management.validation.task_flow_assignment.flow_without_steps"
        ) % {'code': flow.code}})
    return errors
