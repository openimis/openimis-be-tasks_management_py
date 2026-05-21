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
        LAYERED = 'LAYERED', _('LAYERED')

    class RejectionPolicy(models.TextChoices):
        RESET_TO_FIRST = 'RESET_TO_FIRST', _('Reset to first layer')
        RETURN_PREVIOUS = 'RETURN_PREVIOUS', _('Return to previous layer')

    class RejectionScope(models.TextChoices):
        WHOLE_TASK = 'WHOLE_TASK', _('Whole task')
        DISPUTED_ONLY = 'DISPUTED_ONLY', _('Disputed records only')

    code = models.CharField(max_length=255, null=False, blank=False)
    completion_policy = models.CharField(
        max_length=50, choices=TaskGroupCompletionPolicy.choices, null=False, blank=False
    )
    rejection_policy = models.CharField(
        max_length=50, choices=RejectionPolicy.choices, null=True, blank=True
    )
    rejection_scope = models.CharField(
        max_length=50, choices=RejectionScope.choices, null=True, blank=True
    )
    allow_executor_override = models.BooleanField(default=False)


class TaskGroupLayer(HistoryModel):
    class LayerCompletionPolicy(models.TextChoices):
        ALL = 'ALL', _('ALL')
        ANY = 'ANY', _('ANY')
        N = 'N', _('N')

    task_group = models.ForeignKey(
        TaskGroup, on_delete=models.DO_NOTHING, null=False, related_name='layers'
    )
    order = models.PositiveSmallIntegerField(null=False)
    code = models.CharField(max_length=255, null=False, blank=False)
    completion_policy = models.CharField(
        max_length=50, choices=LayerCompletionPolicy.choices, null=False, blank=False
    )
    required_approvals = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(
                fields=['task_group', 'order'],
                name='unique_task_group_layer_order',
            ),
            models.UniqueConstraint(
                fields=['task_group', 'code'],
                name='unique_task_group_layer_code',
            ),
        ]


class TaskExecutor(HistoryModel):
    user = models.ForeignKey(User, on_delete=models.DO_NOTHING, null=False)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.DO_NOTHING, null=False)
    task_group_layer = models.ForeignKey(
        TaskGroupLayer,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name='executors',
    )


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
    current_layer = models.ForeignKey(
        TaskGroupLayer,
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='+',
    )
    current_attempt = models.PositiveSmallIntegerField(default=1)


class TaskLayerProgress(HistoryModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        ACTIVE = 'ACTIVE', _('Active')
        APPROVED = 'APPROVED', _('Approved')
        REJECTED = 'REJECTED', _('Rejected')
        SUPERSEDED = 'SUPERSEDED', _('Superseded')

    task = models.ForeignKey(
        Task, on_delete=models.DO_NOTHING, null=False, related_name='layer_progress'
    )
    layer = models.ForeignKey(
        TaskGroupLayer,
        on_delete=models.DO_NOTHING,
        null=False,
        related_name='progress_rows',
    )
    attempt = models.PositiveSmallIntegerField(null=False, default=1)
    status = models.CharField(
        max_length=50, choices=Status.choices, default=Status.PENDING, null=False
    )
    activated_at = models.DateTimeField(blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='+',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'layer', 'attempt'],
                name='unique_task_layer_attempt',
            ),
        ]


class TaskMutation(UUIDModel, ObjectMutation):
    task = models.ForeignKey(Task, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='task')
