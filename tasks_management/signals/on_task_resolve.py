import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from core.forms import User
from tasks_management.apps import TasksManagementConfig
from tasks_management.models import Task, TaskDecision, TaskExecutor, TaskFlowStep
from tasks_management.services import TaskService

logger = logging.getLogger(__name__)

# Whole-task verdicts accepted on the flow path. REJECTED is reserved for
# per-record verdicts (flat batch tasks); rewind semantics arrive with v1.1.
_FLOW_VERDICTS = (TaskDecision.Decision.APPROVED, TaskDecision.Decision.FAILED)


def resolve_task_all(_task, _user):
    if 'FAILED' in _task.business_status.values():
        TaskService(_user).complete_task({"id": _task.id, 'failed': True})
    n_of_approves = sum(map('APPROVED'.__eq__, _task.business_status.values()))
    n_of_executors = _task.task_group.taskexecutor_set.filter(task_group__is_deleted=False, is_deleted=False).count()
    if not n_of_executors:
        logger.warning("No valid executors of task with policy ALL %s", str(_task.uuid))
    if n_of_approves == n_of_executors:
        TaskService(_user).complete_task({"id": _task.id})


def resolve_task_any(_task, _user):
    if 'FAILED' in _task.business_status.values():
        TaskService(_user).complete_task({"id": _task.id, 'failed': True})
    if 'APPROVED' in _task.business_status.values():
        TaskService(_user).complete_task({"id": _task.id})


def resolve_task_n(_task, _user):
    # TODO for now hardcoded to any, to be updated
    resolve_task_any(_task, _user)


def _record_flat_decisions(_task, _user, _verdict):
    """
    Insert-only ledger mirror of the caller's flat-task vote. Additive
    observability for flat tasks: a failure here must never block the
    legacy resolution path, so errors are logged loudly and swallowed.
    """
    try:
        if isinstance(_verdict, str):
            if _verdict not in _FLOW_VERDICTS:
                return
            exists = TaskDecision.objects.filter(
                task=_task, flow_step__isnull=True, user=_user,
                record_id__isnull=True, is_deleted=False,
            ).exists()
            if not exists:
                TaskDecision(
                    task=_task, user=_user, decision=_verdict,
                ).save(username=_user.login_name)
        elif isinstance(_verdict, dict):
            per_record = (
                (TaskDecision.Decision.APPROVED, _verdict.get('ACCEPT') or []),
                (TaskDecision.Decision.REJECTED, _verdict.get('REJECT') or []),
            )
            for decision, record_ids in per_record:
                for record_id in record_ids:
                    record_id = str(record_id)
                    exists = TaskDecision.objects.filter(
                        task=_task, flow_step__isnull=True, user=_user,
                        record_id=record_id, is_deleted=False,
                    ).exists()
                    if not exists:
                        TaskDecision(
                            task=_task, user=_user, decision=decision, record_id=record_id,
                        ).save(username=_user.login_name)
    except Exception as exc:
        logger.error(
            "tasks_management.flow: failed to mirror flat vote into TaskDecision "
            "for task %s user %s", _task.id, _user.id, exc_info=exc,
        )


def _is_privileged_resolver(_user):
    # Mirrors the visibility rule in gql_queries: imis admins and task-triage
    # users see (and may act on) every task.
    from tasks_management.gql_queries import is_task_triage
    return getattr(_user, 'is_imis_admin', False) or is_task_triage(_user)


def _validate_flow_vote(_task, _step, _user, _verdict):
    if isinstance(_verdict, dict):
        raise ValidationError(
            "tasks_management.flow: source '%s' submits per-record votes and cannot "
            "use flows - remove the flow binding or add '%s' to "
            "flow_ineligible_sources" % (_task.source, _task.source)
        )
    if _verdict not in _FLOW_VERDICTS:
        raise ValidationError(
            "tasks_management.flow: unsupported verdict '%s' for a flow task - "
            "accepted values: %s" % (_verdict, ", ".join(_FLOW_VERDICTS))
        )
    is_executor = TaskExecutor.objects.filter(
        task_group_id=_step.task_group_id, user=_user,
        is_deleted=False, task_group__is_deleted=False,
    ).exists()
    if not is_executor and not _is_privileged_resolver(_user):
        raise ValidationError(
            "tasks_management.flow: user '%s' is not an executor of group '%s' for "
            "the current step and has no triage rights" % (_user.id, _step.task_group.code)
        )


def _evaluate_step(_task, _step, _user):
    """
    Evaluate the current step under the caller's task row lock.
    Returns 'failed', 'completed', or None (step still open / advanced).
    """
    decisions = TaskDecision.objects.filter(
        task=_task, flow_step=_step, record_id__isnull=True, is_deleted=False,
    )
    # Any FAILED verdict fails the whole task - explicit short-circuit, no
    # fall-through to the approval branch.
    if decisions.filter(decision=TaskDecision.Decision.FAILED).exists():
        logger.info(
            "tasks_management.flow: task %s FAILED at step %s (order %s)",
            _task.id, _step.id, _step.order,
        )
        return 'failed'

    executor_count = _step.task_group.taskexecutor_set.filter(
        task_group__is_deleted=False, is_deleted=False,
    ).count()
    if executor_count == 0:
        # An empty pool must never instant-pass ALL (0 >= 0); the task holds
        # here until the pool is refilled. Recovery: re_evaluate on any
        # subsequent (duplicate) vote or the ops management command.
        logger.error(
            "tasks_management.flow: task %s held at step %s (order %s) - executor "
            "pool '%s' is empty; refill the pool to resume",
            _task.id, _step.id, _step.order, _step.task_group.code,
        )
        return None

    approvals = decisions.filter(decision=TaskDecision.Decision.APPROVED).count()
    policy = _step.effective_policy()
    threshold = _step.effective_threshold()
    if policy == TaskFlowStep.StepCompletionPolicy.ALL:
        # >= not ==: pool shrink after votes were cast must not strand the task
        passed = approvals >= executor_count
    elif policy == TaskFlowStep.StepCompletionPolicy.ANY:
        passed = approvals >= 1
    elif policy == TaskFlowStep.StepCompletionPolicy.N:
        if not threshold:
            logger.error(
                "tasks_management.flow: task %s step %s has policy N without a "
                "threshold; holding", _task.id, _step.id,
            )
            return None
        passed = approvals >= threshold
    else:
        logger.error(
            "tasks_management.flow: task %s step %s has unknown policy '%s'; holding",
            _task.id, _step.id, policy,
        )
        return None

    if not passed:
        return None

    # Advance within the task's pinned flow version - steps are read through
    # the FK only, never re-resolved through the flow code (version pinning).
    next_step = TaskFlowStep.objects.filter(
        flow_id=_task.flow_id, is_deleted=False, order__gt=_step.order,
    ).select_related('task_group').order_by('order').first()

    if next_step is None:
        # Terminal step passed. current_step deliberately keeps pointing at
        # the terminal step so the FE stepper can render the finished state.
        logger.info(
            "tasks_management.flow: task %s passed terminal step %s (order %s)",
            _task.id, _step.id, _step.order,
        )
        return 'completed'

    _task.current_step = next_step
    # Repointing task_group keeps executor-based visibility, counting and the
    # FE working unchanged: the task now belongs to the next step's pool.
    _task.task_group = next_step.task_group
    _task.save(username=_user.login_name)
    logger.info(
        "tasks_management.flow: task %s advanced from step order %s to %s (pool '%s')",
        _task.id, _step.order, next_step.order, next_step.task_group.code,
    )
    next_pool_size = next_step.task_group.taskexecutor_set.filter(
        task_group__is_deleted=False, is_deleted=False,
    ).count()
    if next_pool_size == 0:
        logger.error(
            "tasks_management.flow: task %s advanced into empty pool '%s' (order %s) "
            "- held until the pool is refilled",
            _task.id, next_step.task_group.code, next_step.order,
        )
    return None


def re_evaluate_flow_task(_task_id, _user):
    """
    Ops re-evaluation of a flow task's current step without recording a vote.
    Used by the re_evaluate_task_step management command, e.g. after refilling
    an emptied executor pool, so held tasks resume without a synthetic re-vote.
    """
    outcome = None
    with transaction.atomic():
        _task = Task.objects.select_for_update(of=('self',)).select_related(
            'current_step__task_group', 'current_step', 'flow',
        ).get(id=_task_id)
        if _task.status != Task.Status.ACCEPTED or not _task.flow_id or not _task.current_step_id:
            logger.warning(
                "tasks_management.flow: task %s is not an in-review flow task; "
                "nothing to re-evaluate", _task_id,
            )
            return
        outcome = _evaluate_step(_task, _task.current_step, _user)
    if outcome == 'failed':
        TaskService(_user).complete_task({"id": _task_id, 'failed': True})
    elif outcome == 'completed':
        TaskService(_user).complete_task({"id": _task_id})


def resolve_flow_task(_task_id, _user, _verdict):
    """
    Flow branch of task resolution. Vote recording, step evaluation and
    advancement run inside one transaction under select_for_update on the
    task row; completion runs after the lock is released so consumer
    handlers never execute third-party writes inside the vote transaction.
    ValidationErrors deliberately propagate to the mutation layer.
    """
    outcome = None
    with transaction.atomic():
        # of=('self',): lock only the task row - FOR UPDATE cannot be applied
        # to the nullable side of the outer joins select_related introduces.
        _task = Task.objects.select_for_update(of=('self',)).select_related(
            'current_step__task_group', 'current_step', 'flow',
        ).get(id=_task_id)

        if _task.status != Task.Status.ACCEPTED:
            logger.warning(
                "tasks_management.flow: task %s no longer ACCEPTED (%s); vote ignored",
                _task.id, _task.status,
            )
            return
        step = _task.current_step
        if step is None:
            logger.error(
                "tasks_management.flow: task %s has flow %s but no current_step; "
                "cannot evaluate", _task.id, _task.flow_id,
            )
            return

        _validate_flow_vote(_task, step, _user, _verdict)

        # Duplicate votes are detected check-then-insert under the lock (an
        # IntegrityError inside this atomic block would poison the
        # transaction) and still re-run evaluation: a re-vote is the
        # documented backstop that resumes a task held on an emptied pool.
        exists = TaskDecision.objects.filter(
            task=_task, flow_step=step, user=_user,
            record_id__isnull=True, is_deleted=False,
        ).exists()
        if exists:
            logger.warning(
                "tasks_management.flow: duplicate vote by user %s on task %s step %s; "
                "re-evaluating", _user.id, _task.id, step.id,
            )
        else:
            TaskDecision(
                task=_task, flow_step=step, user=_user, decision=_verdict,
            ).save(username=_user.login_name)

        outcome = _evaluate_step(_task, step, _user)

    if outcome == 'failed':
        TaskService(_user).complete_task({"id": _task.id, 'failed': True})
    elif outcome == 'completed':
        TaskService(_user).complete_task({"id": _task.id})


def on_task_resolve(**kwargs):
    """
    Generic event for checking the completion_policy of a task. If the task is
    completed or failed, TaskService.complete_task is called with the
    appropriate `failed` flag. Tasks with a flow follow the layered path;
    flat tasks follow the legacy path unchanged.
    """
    try:
        result = kwargs.get('result', None)
        if not (result and result['success']
                and result['data']['task']['status'] == Task.Status.ACCEPTED):
            return
        payload_task = result['data']['task']
        if payload_task['executor_action_event'] != TasksManagementConfig.default_executor_event:
            if payload_task.get('flow'):
                logger.error(
                    "tasks_management.flow: flow task %s resolved with non-default "
                    "executor_action_event '%s' - flows only support the default "
                    "event; the task will not advance",
                    payload_task.get('id'), payload_task['executor_action_event'],
                )
            return
        data = kwargs.get("result").get("data")
        task = Task.objects.select_related('task_group').prefetch_related('task_group__taskexecutor_set').get(
            id=data["task"]["id"])
        user = User.objects.get(id=data["user"]["id"])
        # The caller's verdict comes from the signal payload (the in-memory
        # post-merge representation, which always contains this caller's key)
        # - NOT from a DB re-read, which loses votes under concurrent merges.
        verdict = (payload_task.get('business_status') or {}).get(str(user.id))
    except Exception as e:
        logger.error("Error while executing on_task_resolve", exc_info=e)
        return [str(e)]

    if task.flow_id:
        # ValidationErrors must reach journalize/MutationLog - no swallowing.
        return resolve_flow_task(task.id, user, verdict)

    try:
        _record_flat_decisions(task, user, verdict)

        if not task.task_group:
            logger.error("Resolving task not assigned to TaskGroup: %s", data['task']['id'])
            return ['Task not assigned to TaskGroup']

        resolvers = {
            'ALL': resolve_task_all,
            'ANY': resolve_task_any,
            'N': resolve_task_n,
        }

        if task.task_group.completion_policy not in resolvers:
            logger.error("Resolving task with unknown completion_policy: %s", task.task_group.completion_policy)
            return ['Unknown completion_policy: %s' % task.task_group.completion_policy]

        resolvers[task.task_group.completion_policy](task, user)
    except Exception as e:
        logger.error("Error while executing on_task_resolve", exc_info=e)
        return [str(e)]
