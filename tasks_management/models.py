from django.db import models

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from core.models import HistoryModel, User, UUIDModel, ObjectMutation, MutationLog


class TaskGroup(HistoryModel):
    class TaskGroupCompletionPolicy(models.TextChoices):
        ALL = 'ALL', _('ALL')
        ANY = 'ANY', _('ANY')
        N = 'N', _('N')

    code = models.CharField(max_length=255, null=False, blank=False)
    completion_policy = models.CharField(
        max_length=50, choices=TaskGroupCompletionPolicy.choices, null=False, blank=False
    )
    threshold = models.PositiveSmallIntegerField(null=True, blank=True)


class TaskFlow(HistoryModel):
    code = models.CharField(max_length=255, null=False, blank=False, unique=True)
    name = models.CharField(max_length=255, blank=True, default='')


class TaskFlowStep(HistoryModel):
    class StepCompletionPolicy(models.TextChoices):
        ALL = 'ALL', _('ALL')
        ANY = 'ANY', _('ANY')
        N = 'N', _('N')

    flow = models.ForeignKey(
        TaskFlow, on_delete=models.DO_NOTHING, null=False, related_name='steps'
    )
    task_group = models.ForeignKey(
        TaskGroup, on_delete=models.DO_NOTHING, null=False, related_name='flow_steps'
    )
    order = models.PositiveSmallIntegerField(null=False)
    completion_policy = models.CharField(
        max_length=50, choices=StepCompletionPolicy.choices, null=False, blank=False
    )
    threshold = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(
                fields=['flow', 'order'],
                name='unique_task_flow_step_order',
            ),
        ]


class TaskExecutor(HistoryModel):
    user = models.ForeignKey(User, on_delete=models.DO_NOTHING, null=False)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.DO_NOTHING, null=False)


class Task(HistoryModel):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', _('Received')
        ACCEPTED = 'ACCEPTED', _('Accepted')
        COMPLETED = 'COMPLETED', _('Completed')
        FAILED = 'FAILED', _('Failed')

    source = models.CharField(max_length=255,  blank=True, null=True)
    entity_type = models.ForeignKey(ContentType, models.DO_NOTHING, blank=True, null=True, unique=False)
    entity_id = models.CharField(max_length=255,  blank=True, null=True)
    entity = GenericForeignKey('entity_type', 'entity_id')
    status = models.CharField(max_length=255, choices=Status.choices, default=Status.RECEIVED)
    executor_action_event = models.CharField(max_length=255, blank=True, null=True)
    business_status = models.JSONField( blank=True,default=dict)
    business_event = models.CharField(max_length=255, blank=True, null=True)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.DO_NOTHING,  blank=True, null=True)
    data = models.JSONField(blank=True, default=dict)
    business_data_serializer = models.CharField(max_length=255, blank=True, null=True)
    flow = models.ForeignKey(
        TaskFlow, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='+'
    )
    current_step = models.ForeignKey(
        TaskFlowStep, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='+'
    )


class TaskDecision(HistoryModel):
    task = models.ForeignKey(
        Task, on_delete=models.DO_NOTHING, null=False, related_name='decisions'
    )
    flow_step = models.ForeignKey(
        TaskFlowStep, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='+'
    )
    user = models.ForeignKey(
        User, on_delete=models.DO_NOTHING, null=False, related_name='+'
    )
    decision = models.CharField(max_length=50, null=False, blank=False)
    record_id = models.CharField(max_length=255, blank=True, null=True)
    decided_at = models.DateTimeField(null=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'flow_step', 'user', 'record_id'],
                name='unique_decision_per_slot_user_record',
            ),
        ]


class TaskMutation(UUIDModel, ObjectMutation):
    task = models.ForeignKey(Task, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='task')
