from django.db import models

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
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

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(completion_policy='N', threshold__gte=1)
                    | (~Q(completion_policy='N') & Q(threshold__isnull=True))
                ),
                name='task_group_threshold_matches_policy',
            ),
        ]


class TaskFlow(HistoryModel):
    """
    Reusable, ordered approval flow. Tasks reference it via Task.flow and
    advance step by step; the group-level completion_policy only applies to
    flat (non-flow) tasks. As a flow advances, Task.task_group is kept
    pointing at the active step's pool so executor-based visibility and
    filtering keep working unchanged.
    """
    code = models.CharField(max_length=255, null=False, blank=False)
    name = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['code'],
                condition=Q(is_deleted=False),
                name='unique_task_flow_code',
            ),
        ]


class TaskFlowStep(HistoryModel):
    """
    One ordered step of a TaskFlow, pointing at a TaskGroup used as an
    executor pool. The step's own completion_policy/threshold decide when
    the step completes - the pool's group-level policy is ignored inside a
    flow, so one group can act as ALL in one flow and ANY in another.
    """
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
                condition=Q(is_deleted=False),
                name='unique_task_flow_step_order',
            ),
            models.CheckConstraint(
                check=(
                    Q(completion_policy='N', threshold__gte=1)
                    | (~Q(completion_policy='N') & Q(threshold__isnull=True))
                ),
                name='task_flow_step_threshold_matches_policy',
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
        TaskFlow, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='tasks'
    )
    current_step = models.ForeignKey(
        TaskFlowStep, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='tasks_at_step'
    )


class TaskDecision(HistoryModel):
    """
    Insert-only vote ledger: one row per (task, step, user, record) decision.
    flow_step is NULL for flat (non-flow) tasks; record_id is NULL for
    whole-task decisions. Rewind/amend-and-resubmit is out of scope for now;
    when it lands, a round column joins the uniqueness key so earlier steps
    can be re-voted.
    """

    class Decision(models.TextChoices):
        APPROVED = 'APPROVED', _('Approved')
        REJECTED = 'REJECTED', _('Rejected')
        FAILED = 'FAILED', _('Failed')

    task = models.ForeignKey(
        Task, on_delete=models.DO_NOTHING, null=False, related_name='decisions'
    )
    flow_step = models.ForeignKey(
        TaskFlowStep, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='decisions'
    )
    user = models.ForeignKey(
        User, on_delete=models.DO_NOTHING, null=False, related_name='+'
    )
    decision = models.CharField(
        max_length=50, choices=Decision.choices, null=False, blank=False
    )
    record_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        # NULLs compare distinct in unique constraints, so each NULL
        # combination of (flow_step, record_id) needs its own partial
        # constraint; is_deleted=False keeps a retracted vote from
        # blocking a re-vote.
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'flow_step', 'user', 'record_id'],
                condition=Q(is_deleted=False),
                name='unique_decision_step_user_record',
            ),
            models.UniqueConstraint(
                fields=['task', 'flow_step', 'user'],
                condition=Q(is_deleted=False, record_id__isnull=True),
                name='unique_decision_step_user_whole_task',
            ),
            models.UniqueConstraint(
                fields=['task', 'user', 'record_id'],
                condition=Q(is_deleted=False, flow_step__isnull=True),
                name='unique_decision_flat_user_record',
            ),
            models.UniqueConstraint(
                fields=['task', 'user'],
                condition=Q(is_deleted=False, flow_step__isnull=True, record_id__isnull=True),
                name='unique_decision_flat_user_whole_task',
            ),
        ]


class TaskMutation(UUIDModel, ObjectMutation):
    task = models.ForeignKey(Task, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='task')
