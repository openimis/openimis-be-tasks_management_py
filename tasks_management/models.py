from django.db import models

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import HistoryModel, HistoryBusinessModel, User, UUIDModel, ObjectMutation, MutationLog


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
                # threshold__isnull=False is required, not redundant with
                # __gte=1: Postgres treats a NULL check-expression result as
                # passing, and 'N' AND (NULL >= 1) evaluates to NULL, not
                # FALSE - a policy=N row with threshold=NULL would otherwise
                # slip through silently instead of being rejected.
                check=(
                    Q(completion_policy='N', threshold__isnull=False, threshold__gte=1)
                    | (~Q(completion_policy='N') & Q(threshold__isnull=True))
                ),
                name='task_group_threshold_matches_policy',
            ),
        ]


class TaskFlow(HistoryBusinessModel):
    """
    Reusable, ordered approval flow. Tasks reference it via Task.flow and
    advance step by step; the group-level completion_policy only applies to
    flat (non-flow) tasks. As a flow advances, Task.task_group is kept
    pointing at the active step's pool so executor-based visibility and
    filtering keep working unchanged.

    Versioned via HistoryBusinessModel: semantic edits must go through
    TaskFlowService.replace(), which supersedes this row (replacement_uuid
    set, further updates blocked by core) and creates a new head version, so
    in-flight tasks keep following the version they started on. The service
    performs that supersession itself rather than calling core's
    replace_object(), which would trip the head-code uniqueness constraint
    below. Replacing a flow does not clone its steps - the service re-creates
    TaskFlowStep rows for the new version.
    """
    code = models.CharField(max_length=255, null=False, blank=False)
    name = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        constraints = [
            # Head versions only: a superseded row keeps is_deleted=False,
            # so scoping by is_deleted alone would false-fire on replace.
            models.UniqueConstraint(
                fields=['code'],
                condition=Q(is_deleted=False, replacement_uuid__isnull=True),
                name='unique_task_flow_code',
            ),
        ]


class TaskFlowStep(HistoryBusinessModel):
    """
    One ordered step of a TaskFlow, pointing at a TaskGroup used as an
    executor pool. completion_policy/threshold are optional overrides:
    NULL inherits the pool group's values (live read), setting them pins
    the step's policy - so one group can act as ALL in one flow and ANY
    in another. Versioned via HistoryBusinessModel like TaskFlow;
    Task.current_step pins the exact step version a task is on.
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
        max_length=50, choices=StepCompletionPolicy.choices, null=True, blank=True
    )
    threshold = models.PositiveSmallIntegerField(null=True, blank=True)

    def effective_policy(self):
        return self.completion_policy or self.task_group.completion_policy

    def effective_threshold(self):
        return self.threshold if self.completion_policy else self.task_group.threshold

    class Meta:
        ordering = ['order']
        constraints = [
            # Deliberate v1 limitation: one group per tier - parallel
            # independent sign-offs at the same order are not supported.
            models.UniqueConstraint(
                fields=['flow', 'order'],
                condition=Q(is_deleted=False, replacement_uuid__isnull=True),
                name='unique_task_flow_step_order',
            ),
            # CHECK passes on UNKNOWN, so every branch pins the NULLness of
            # completion_policy AND of threshold - threshold__gte=1 alone
            # evaluates to NULL (not FALSE) when threshold is NULL, which
            # would let a policy=N, threshold=NULL row silently through.
            models.CheckConstraint(
                check=(
                    Q(completion_policy__isnull=True, threshold__isnull=True)
                    | Q(completion_policy='N', threshold__isnull=False, threshold__gte=1)
                    | Q(completion_policy__in=['ALL', 'ANY'], threshold__isnull=True)
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

    class Meta:
        constraints = [
            # One-directional on purpose: a finished flow task may clear its
            # step, but a flat task must never carry one. The cross-table
            # invariant (current_step belongs to flow) lives in the service
            # layer.
            models.CheckConstraint(
                check=Q(flow__isnull=False) | Q(current_step__isnull=True),
                name='task_current_step_requires_flow',
            ),
        ]


class TaskDecision(HistoryModel):
    """
    Insert-only vote ledger: one row per (task, step, user, record) decision.
    flow_step is NULL for flat (non-flow) tasks; record_id is NULL for
    whole-task decisions. REJECTED is a reviewer's verdict against the item
    (per-record or whole-task); FAILED is the whole-task failure verdict kept
    for parity with the existing business_status vocabulary - the resolver
    maps it to complete_task(failed=True). Rewind/amend-and-resubmit is out
    of scope for now; when it lands, a round column joins the uniqueness key
    so earlier steps can be re-voted.
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
        # Step evaluation reads all decisions for (task, current_step) on
        # every vote; the partial unique indexes below don't serve that
        # lookup, so it gets a plain index.
        indexes = [
            models.Index(fields=['task', 'flow_step'], name='idx_task_decision_task_step'),
        ]
        # NULLs compare distinct in unique constraints, so the four
        # (flow_step, record_id) NULL combinations are partitioned into
        # mutually exclusive partial constraints; is_deleted=False keeps
        # a retracted vote from blocking a re-vote.
        constraints = [
            models.UniqueConstraint(
                fields=['task', 'flow_step', 'user', 'record_id'],
                condition=Q(is_deleted=False, flow_step__isnull=False, record_id__isnull=False),
                name='unique_decision_step_user_record',
            ),
            models.UniqueConstraint(
                fields=['task', 'flow_step', 'user'],
                condition=Q(is_deleted=False, flow_step__isnull=False, record_id__isnull=True),
                name='unique_decision_step_user_whole_task',
            ),
            models.UniqueConstraint(
                fields=['task', 'user', 'record_id'],
                condition=Q(is_deleted=False, flow_step__isnull=True, record_id__isnull=False),
                name='unique_decision_flat_user_record',
            ),
            models.UniqueConstraint(
                fields=['task', 'user'],
                condition=Q(is_deleted=False, flow_step__isnull=True, record_id__isnull=True),
                name='unique_decision_flat_user_whole_task',
            ),
            # record_id is optional (whole-task decisions) but must never be
            # an empty string, or it would dodge the isnull partitioning above.
            models.CheckConstraint(
                check=Q(record_id__isnull=True) | ~Q(record_id=''),
                name='task_decision_record_id_not_blank',
            ),
        ]


class TaskMutation(UUIDModel, ObjectMutation):
    task = models.ForeignKey(Task, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='task')
