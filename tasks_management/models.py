import logging

from django.db import models

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import HistoryModel, HistoryBusinessModel, User, UUIDModel, ObjectMutation, MutationLog
from core.models import ParentScope

logger = logging.getLogger(__name__)


def entity_display_string(entity):
    """
    The text the `entityString_Icontains` task filter searches: `str(entity)`, the
    same text the GraphQL `entityString` field shows, or None when the task has no
    entity (or its entity no longer exists).

    A broken `__str__` on some entity model must not prevent saving the task: the
    task is then simply not found by an entity search, and the failure is logged.
    """
    if entity is None:
        return None
    try:
        return str(entity)
    except Exception:
        logger.warning(
            "tasks_management: cannot build the entity string of %s %s",
            type(entity).__name__, getattr(entity, "pk", None), exc_info=True,
        )
        return None


def refresh_entity_strings(queryset, batch_size=500):
    """
    Recompute `entity_string` for the rows of `queryset` (tasks or historical
    tasks) from their current entity, in batches. Returns the number of rows
    whose text changed.

    Rows are updated with `bulk_update`, so this neither bumps the task version
    nor writes history: it is a cache refresh, not a change of the task. Used by
    the backfill migration and the `refresh_task_entity_string` command.

    The entity is looked up per batch and per content type with one query, like
    `prefetch_related("entity")` would; this also works on the historical model,
    which carries no GenericForeignKey. `str()` of some entities (a payroll, an
    upload record) still reads related rows of its own, which is why this runs
    once here rather than on every search.
    """
    queryset = queryset.order_by("pk")
    changed = 0
    last_pk = None
    while True:
        batch_qs = queryset if last_pk is None else queryset.filter(pk__gt=last_pk)
        batch = list(batch_qs[:batch_size])
        if not batch:
            return changed
        last_pk = batch[-1].pk

        ids_by_type = {}
        for row in batch:
            if row.entity_type_id and row.entity_id:
                ids_by_type.setdefault(row.entity_type_id, set()).add(row.entity_id)
        entities = {}
        for type_id, entity_ids in ids_by_type.items():
            model = ContentType.objects.get_for_id(type_id).model_class()
            if model is None:
                continue
            keys = {}
            for entity_id in entity_ids:
                # Keyed by the raw stored id, whatever its spelling (a UUID in
                # upper case, say), as the GenericForeignKey resolves it.
                try:
                    keys[entity_id] = model._meta.pk.to_python(entity_id)
                except ValidationError:
                    continue  # not a valid key for this model: no entity
            found = model._base_manager.in_bulk(set(keys.values()))
            for entity_id, key in keys.items():
                entities[(type_id, entity_id)] = found.get(key)

        to_update = []
        for row in batch:
            entity = entities.get((row.entity_type_id, row.entity_id))
            text = entity_display_string(entity)
            if row.entity_string != text:
                row.entity_string = text
                to_update.append(row)
        if to_update:
            queryset.model._base_manager.bulk_update(to_update, ["entity_string"])
            changed += len(to_update)


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

    @classmethod
    def get_rights(cls, action):
        """Access point to the rights of the `taskGroup` entity."""
        from tasks_management.apps import configured_perms

        return configured_perms("taskGroup", action)

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

    @classmethod
    def get_rights(cls, action):
        """
        Access point to the rights of the `taskFlow` entity.

        `replace` is not a distinct action: replacing a flow is the versioned form of
        modifying it, and the mutation checks the `update` right.
        """
        from tasks_management.apps import configured_perms

        return configured_perms("taskFlow", action)

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
    # A step is not an object one holds rights on separately: creating it, reordering
    # it or deleting it means composing the flow, and the mutations confirm it -
    # createTaskFlow / updateTaskFlow / replaceTaskFlow all check a `taskFlow` right,
    # never a `taskGroup` one. `scope_parent` therefore denotes `flow` and not
    # `task_group`: the latter is the *pool* the step references, a group shared
    # between flows, and does not govern who may compose this particular flow. The
    # parent is declared and not inferred, precisely because the two FKs look alike.
    scope_parent = "flow"

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
    # `row_scope` (which rows) and `scope_parent` (which rights) answer two different
    # questions and do not target the same FK, hence the two declarations.
    row_scope = ParentScope("user")

    # Membership of a group has no rights of its own: it is neither created nor
    # deleted by a mutation of its own - `TaskGroupService.create/update/delete` is
    # what lays down and removes the rows from the group mutation's `user_ids` - and
    # the only query that exposes it (`resolve_task_executor`) already checks
    # `gql_task_group_search_perms`. `scope_parent` therefore denotes `task_group` and
    # not `user`: `user` says *whose* the row is (that is `row_scope`'s job above), not
    # who has the right to compose this group.
    scope_parent = "task_group"

    user = models.ForeignKey(User, on_delete=models.DO_NOTHING, null=False)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.DO_NOTHING, null=False)


class Task(HistoryModel):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', _('Received')
        ACCEPTED = 'ACCEPTED', _('Accepted')
        COMPLETED = 'COMPLETED', _('Completed')
        FAILED = 'FAILED', _('Failed')

    source = models.CharField(max_length=255, blank=True, null=True)
    entity_type = models.ForeignKey(ContentType, models.DO_NOTHING, blank=True, null=True, unique=False)
    entity_id = models.CharField(max_length=255, blank=True, null=True)
    entity = GenericForeignKey('entity_type', 'entity_id')
    # `str(entity)` as of the task's last save, so that `entityString_Icontains`
    # filters in SQL instead of resolving every task's entity in Python. Set by
    # save(); the GraphQL `entityString` field still resolves the live entity.
    entity_string = models.TextField(blank=True, null=True, editable=False)
    status = models.CharField(max_length=255, choices=Status.choices, default=Status.RECEIVED)
    executor_action_event = models.CharField(max_length=255, blank=True, null=True)
    business_status = models.JSONField(blank=True, default=dict)
    business_event = models.CharField(max_length=255, blank=True, null=True)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.DO_NOTHING, blank=True, null=True)
    data = models.JSONField(blank=True, default=dict)
    business_data_serializer = models.CharField(max_length=255, blank=True, null=True)
    flow = models.ForeignKey(
        TaskFlow, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='tasks'
    )
    current_step = models.ForeignKey(
        TaskFlowStep, on_delete=models.DO_NOTHING, blank=True, null=True, related_name='tasks_at_step'
    )

    def save(self, *args, **kwargs):
        # Refreshed on every save, not only when the entity changes: a task is
        # saved at each status change and vote, so an entity renamed while its
        # task is open is picked up at the next step. Between two saves the
        # text is a snapshot (see `refresh_task_entity_string` to resync).
        self.entity_string = entity_display_string(self._entity_or_none())
        return super().save(*args, **kwargs)

    def _entity_or_none(self):
        """
        The entity as stored now, read from the database rather than through the
        GenericForeignKey: a task served by the model cache still holds the entity
        object it was cached with, so `self.entity` would return the old name.
        """
        if self.entity_type_id is None or not self.entity_id:
            return None
        try:
            model = ContentType.objects.get_for_id(self.entity_type_id).model_class()
            return model._base_manager.filter(pk=self.entity_id).first() if model else None
        except (ValidationError, ValueError):
            return None  # not a valid key for that model: no entity

    @classmethod
    def bulk_save(cls, data_list, user, *args, **kwargs):
        """
        HistoryModel.bulk_save writes with bulk_create/bulk_update, which bypass
        save(): compute each row's entity_string up front so it is written, and
        copied into the history rows, with the rest of the data.
        """
        if not data_list:
            return super().bulk_save(data_list, user, *args, **kwargs)
        ids = [data["id"] for data in data_list if data.get("id")]
        existing = {
            str(task.id): task for task in cls._base_manager.filter(id__in=ids).only("id", "entity_type", "entity_id")
        }
        prepared = []
        for data in data_list:
            probe = cls()
            current = existing.get(str(data.get("id")))
            if current is not None:
                probe.entity_type_id, probe.entity_id = current.entity_type_id, current.entity_id
            for key in ("entity_type", "entity_type_id", "entity_id", "entity"):
                if key in data:
                    setattr(probe, key, data[key])
            prepared.append({**data, "entity_string": entity_display_string(probe._entity_or_none())})
        return super().bulk_save(prepared, user, *args, **kwargs)

    @classmethod
    def get_rights(cls, action):
        """
        Access point to the rights of the `task` entity.

        `create` and `delete` do answer, but no server entry point queries them today
        (see the note on dormant declarations in `tasks_management.apps`). `resolve` is
        not declared: voting on a task is governed by membership of the executing
        group, not by a right - so the method returns None and the caller must fail
        closed.
        """
        from tasks_management.apps import configured_perms

        return configured_perms("task", action)

    @classmethod
    def get_queryset(cls, queryset, user=None):
        """
        The tasks `user` may see: all of them for triage, otherwise those of the
        groups the user is a live executor of, once they have left RECEIVED.

        Not a location scope, so not a `row_scope`. Also applied to history rows
        (`TaskHistoryGQLType`), which carry the same fields. Membership goes
        through a subquery rather than a join, so a user holding several executor
        rows for one group does not duplicate tasks.
        """
        from tasks_management.gql_queries import is_task_triage

        user = cls.scoping_user(user)
        if queryset is None:
            queryset = cls.objects.all()
        queryset = queryset.filter(is_deleted=False)
        if user is None or getattr(user, "is_anonymous", True):
            return queryset.none()
        if user.is_superuser or is_task_triage(user):
            return queryset
        executor_groups = TaskGroup.objects.filter(
            taskexecutor__user=user, taskexecutor__is_deleted=False
        )
        return queryset.filter(task_group__in=executor_groups).exclude(
            status=cls.Status.RECEIVED
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

    # A vote is not an object with rights of its own: it is only written by the
    # resolution of a task (`signals/on_task_resolve`) and its GraphQL visibility
    # already copies the task's. `scope_parent` denotes `task` among the three FKs:
    # `flow_step` is the step *where* the vote was cast and `user` its author, and
    # neither governs who may read or write this task's record.
    scope_parent = "task"

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
