from django.core.exceptions import ValidationError
from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from tasks_management.apps import TasksManagementConfig
from tasks_management.models import (
    Task,
    TaskDecision,
    TaskExecutor,
    TaskFlow,
    TaskFlowStep,
    TaskGroup,
)
from tasks_management.services import TaskService


class FlowResolverTestCase(TestCase):
    """
    W2 coverage: flow-branch resolution (vote ledger, step evaluation with
    inheritance, advancement + task_group repointing, hold/recovery, loud
    validation errors) and flat-path regression incl. the TaskDecision
    dual-write. Drives the real service + signal chain via resolve_task.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = create_test_interactive_user(username="flow_admin")
        cls.exec_a = create_test_interactive_user(username="flow_exec_a")
        cls.exec_b = create_test_interactive_user(username="flow_exec_b")
        cls.exec_c = create_test_interactive_user(username="flow_exec_c")
        # Minimal role (Receptionist, role id 2): a real non-executor without
        # triage rights - the default helper roles carry admin-tier perms.
        cls.outsider = create_test_interactive_user(username="flow_outsider", roles=[2])

    def _group(self, code, policy, threshold=None, executors=()):
        group = TaskGroup(code=code, completion_policy=policy, threshold=threshold)
        group.save(username=self.admin.username)
        for user in executors:
            executor = TaskExecutor(task_group=group, user=user)
            executor.save(username=self.admin.username)
        return group

    def _flow(self, code, *steps):
        """steps: tuples of (task_group, completion_policy, threshold)."""
        flow = TaskFlow(code=code, name=code)
        flow.save(username=self.admin.username)
        for order, (group, policy, threshold) in enumerate(steps, start=1):
            step = TaskFlowStep(
                flow=flow, task_group=group, order=order,
                completion_policy=policy, threshold=threshold,
            )
            step.save(username=self.admin.username)
        return flow

    def _flow_task(self, flow, source='flow_test_source'):
        first_step = flow.steps.filter(is_deleted=False).order_by('order').first()
        task = Task(
            source=source,
            status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_event='flow_test_event',
            business_status={},
            data={},
            flow=flow,
            current_step=first_step,
            task_group=first_step.task_group,
        )
        task.save(username=self.admin.username)
        return task

    def _flat_task(self, group):
        task = Task(
            source='flat_test_source',
            status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_event='flat_test_event',
            business_status={},
            data={},
            task_group=group,
        )
        task.save(username=self.admin.username)
        return task

    def _vote(self, task, user, verdict):
        return TaskService(user).resolve_task({
            'id': task.id,
            'business_status': {str(user.id): verdict},
        })

    def _batch_task(self, flow):
        # import_valid_items is a configured batch source: it submits
        # {ACCEPT: [...], REJECT: [...]} per record instead of one verdict.
        return self._flow_task(flow, source='import_valid_items')

    # ----------------------------------------------------------------- batch

    def test_batch_step_advances_on_reviewer_vote(self):
        group1 = self._group('bt1', 'ANY', executors=[self.exec_a])
        group2 = self._group('bt2', 'ANY', executors=[self.exec_b])
        flow = self._flow('f_batch', (group1, None, None), (group2, None, None))
        task = self._batch_task(flow)

        self._vote(task, self.exec_a, {'ACCEPT': ['r1', 'r2'], 'REJECT': ['r3']})

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertEqual(task.current_step.order, 2)
        # the batch moved as a unit and the pool followed the step
        self.assertEqual(task.task_group_id, group2.id)
        self.assertEqual(
            TaskDecision.objects.filter(task=task, is_deleted=False).count(), 3)

    def test_batch_rejections_accumulate_across_steps(self):
        from tasks_management.signals.on_task_resolve import flow_rejected_record_ids

        group1 = self._group('br1', 'ANY', executors=[self.exec_a])
        group2 = self._group('br2', 'ANY', executors=[self.exec_b])
        flow = self._flow('f_batch_rej', (group1, None, None), (group2, None, None))
        task = self._batch_task(flow)

        self._vote(task, self.exec_a, {'ACCEPT': ['r1', 'r2'], 'REJECT': ['r3']})
        task.refresh_from_db()
        self._vote(task, self.exec_b, {'ACCEPT': ['r1'], 'REJECT': ['r2']})

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        # a row rejected at any step stays rejected for the whole flow
        self.assertEqual(flow_rejected_record_ids(task), {'r2', 'r3'})

    def test_batch_step_all_waits_for_every_reviewer(self):
        group1 = self._group('ba1', 'ALL', executors=[self.exec_a, self.exec_b])
        flow = self._flow('f_batch_all', (group1, None, None))
        task = self._batch_task(flow)

        self._vote(task, self.exec_a, {'ACCEPT': ['r1']})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        # ALL counts reviewers who submitted, not approvals of one subject
        self._vote(task, self.exec_b, {'ACCEPT': ['r1']})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)

    def test_batch_source_rejects_whole_task_verdict(self):
        group1 = self._group('bw1', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_batch_whole', (group1, None, None))
        task = self._batch_task(flow)

        with self.assertRaises(ValidationError):
            self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertFalse(TaskDecision.objects.filter(task=task).exists())

    def test_batch_empty_vote_raises(self):
        group1 = self._group('be1', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_batch_empty', (group1, None, None))
        task = self._batch_task(flow)

        with self.assertRaises(ValidationError):
            self._vote(task, self.exec_a, {'ACCEPT': [], 'REJECT': []})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

    def test_batch_duplicate_record_vote_is_idempotent(self):
        group1 = self._group('bd1', 'ALL', executors=[self.exec_a, self.exec_b])
        flow = self._flow('f_batch_dup', (group1, None, None))
        task = self._batch_task(flow)

        self._vote(task, self.exec_a, {'ACCEPT': ['r1']})
        self._vote(task, self.exec_a, {'ACCEPT': ['r1']})

        task.refresh_from_db()
        # the re-vote adds nothing and does not count as a second reviewer
        self.assertEqual(
            TaskDecision.objects.filter(task=task, is_deleted=False).count(), 1)
        self.assertEqual(task.status, Task.Status.ACCEPTED)

    # ------------------------------------------------------------------ flow

    def test_two_step_flow_advances_and_completes(self):
        group1 = self._group('fg1', 'ANY', executors=[self.exec_a])
        group2 = self._group('fg2', 'ANY', executors=[self.exec_b])
        flow = self._flow('f_two_step', (group1, 'ANY', None), (group2, 'ANY', None))
        task = self._flow_task(flow)
        step1 = task.current_step

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertEqual(task.current_step.order, 2)
        self.assertEqual(task.task_group_id, group2.id)
        self.assertNotEqual(task.current_step_id, step1.id)

        self._vote(task, self.exec_b, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        # current_step is retained on completion for terminal FE rendering
        self.assertEqual(task.current_step.order, 2)

    def test_failed_vote_fails_whole_task(self):
        group1 = self._group('ff1', 'ANY', executors=[self.exec_a])
        group2 = self._group('ff2', 'ANY', executors=[self.exec_b])
        flow = self._flow('f_fail', (group1, 'ANY', None), (group2, 'ANY', None))
        task = self._flow_task(flow)

        self._vote(task, self.exec_a, 'FAILED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(task.current_step.order, 1)

    def test_step_policy_override_pins_all(self):
        # Pool group says ANY, the step overrides to ALL with two executors:
        # a single approval must not advance the task.
        group1 = self._group('fo1', 'ANY', executors=[self.exec_a, self.exec_b])
        flow = self._flow('f_override', (group1, 'ALL', None))
        task = self._flow_task(flow)

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        self._vote(task, self.exec_b, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)

    def test_step_inherits_group_policy_when_null(self):
        # Step policy NULL -> inherits the pool group's N/2.
        group1 = self._group('fi1', 'N', threshold=2,
                             executors=[self.exec_a, self.exec_b, self.exec_c])
        flow = self._flow('f_inherit', (group1, None, None))
        task = self._flow_task(flow)

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        self._vote(task, self.exec_b, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)

    def test_dict_vote_on_flow_task_raises(self):
        group1 = self._group('fd1', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_dict', (group1, 'ANY', None))
        task = self._flow_task(flow)

        with self.assertRaises(ValidationError):
            self._vote(task, self.exec_a, {'ACCEPT': ['some-id']})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertFalse(TaskDecision.objects.filter(task=task).exists())

    def test_unmapped_verdict_raises(self):
        group1 = self._group('fu1', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_verdict', (group1, 'ANY', None))
        task = self._flow_task(flow)

        with self.assertRaises(ValidationError):
            self._vote(task, self.exec_a, 'REJECTED')
        self.assertFalse(TaskDecision.objects.filter(task=task).exists())

    def test_non_member_vote_rejected(self):
        group1 = self._group('fm1', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_member', (group1, 'ANY', None))
        task = self._flow_task(flow)

        with self.assertRaises(ValidationError):
            self._vote(task, self.outsider, 'APPROVED')
        self.assertFalse(TaskDecision.objects.filter(task=task).exists())

    def test_privileged_resolver_can_vote_at_current_step(self):
        # Triage/admin votes count as a vote at the current step (D14),
        # never as a force-complete of the whole flow.
        group1 = self._group('fp1', 'ANY', executors=[self.exec_a])
        group2 = self._group('fp2', 'ANY', executors=[self.exec_b])
        flow = self._flow('f_priv', (group1, 'ANY', None), (group2, 'ANY', None))
        task = self._flow_task(flow)

        self._vote(task, self.admin, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertEqual(task.current_step.order, 2)

    def test_duplicate_vote_is_idempotent_and_reevaluates(self):
        # ALL pool of two; A approves (1/2). C is removed from consideration
        # by shrinking the pool; A's duplicate re-vote re-evaluates with
        # >= counting and completes - the documented held-task recovery.
        group1 = self._group('fr1', 'ALL', executors=[self.exec_a, self.exec_b])
        flow = self._flow('f_recover', (group1, 'ALL', None))
        task = self._flow_task(flow)

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertEqual(TaskDecision.objects.filter(task=task).count(), 1)

        TaskExecutor.objects.filter(task_group=group1, user=self.exec_b).delete()

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        self.assertEqual(TaskDecision.objects.filter(task=task).count(), 1)

    def test_empty_pool_never_instant_passes(self):
        # ALL over an empty pool must hold (0 >= 0 would instant-pass), even
        # when a privileged user votes.
        group1 = self._group('fe1', 'ALL', executors=[])
        flow = self._flow('f_empty', (group1, 'ALL', None))
        task = self._flow_task(flow)

        self._vote(task, self.admin, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertEqual(task.current_step.order, 1)

    def test_advance_into_empty_pool_holds_until_refill(self):
        group1 = self._group('fh1', 'ANY', executors=[self.exec_a])
        group2 = self._group('fh2', 'ANY', executors=[])
        flow = self._flow('f_hold', (group1, 'ANY', None), (group2, 'ANY', None))
        task = self._flow_task(flow)

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertEqual(task.current_step.order, 2)

        executor = TaskExecutor(task_group=group2, user=self.exec_b)
        executor.save(username=self.admin.username)
        self._vote(task, self.exec_b, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)

    def test_decisions_ledger_records_step_and_user(self):
        group1 = self._group('fl1', 'ANY', executors=[self.exec_a])
        group2 = self._group('fl2', 'ANY', executors=[self.exec_b])
        flow = self._flow('f_ledger', (group1, 'ANY', None), (group2, 'ANY', None))
        task = self._flow_task(flow)
        step1 = task.current_step

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self._vote(task, self.exec_b, 'APPROVED')

        decisions = TaskDecision.objects.filter(task=task).order_by('date_created')
        self.assertEqual(decisions.count(), 2)
        self.assertEqual(decisions[0].flow_step_id, step1.id)
        self.assertEqual(decisions[0].user_id, self.exec_a.id)
        self.assertEqual(decisions[0].decision, TaskDecision.Decision.APPROVED)
        self.assertEqual(decisions[1].user_id, self.exec_b.id)

    # ------------------------------------------------------------------ flat

    def test_flat_any_completes_and_dual_writes_ledger(self):
        group = self._group('flat_any', 'ANY', executors=[self.exec_a])
        task = self._flat_task(group)

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        decision = TaskDecision.objects.get(task=task)
        self.assertIsNone(decision.flow_step_id)
        self.assertIsNone(decision.record_id)
        self.assertEqual(decision.decision, TaskDecision.Decision.APPROVED)

    def test_flat_all_waits_for_all_and_dual_writes(self):
        group = self._group('flat_all', 'ALL', executors=[self.exec_a, self.exec_b])
        task = self._flat_task(group)

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        self._vote(task, self.exec_b, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        self.assertEqual(
            TaskDecision.objects.filter(task=task, flow_step__isnull=True).count(), 2,
        )

    def test_flat_per_record_votes_dual_write_without_completing(self):
        group = self._group('flat_rec', 'ANY', executors=[self.exec_a])
        task = self._flat_task(group)

        TaskService(self.exec_a).resolve_task({
            'id': task.id,
            'business_status': {
                str(self.exec_a.id): {'ACCEPT': ['rec-1'], 'REJECT': ['rec-2']},
            },
        })
        task.refresh_from_db()
        # Dict verdicts never complete a flat task (legacy parity)
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        rows = TaskDecision.objects.filter(task=task).order_by('record_id')
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows[0].record_id, 'rec-1')
        self.assertEqual(rows[0].decision, TaskDecision.Decision.APPROVED)
        self.assertEqual(rows[1].record_id, 'rec-2')
        self.assertEqual(rows[1].decision, TaskDecision.Decision.REJECTED)

    def test_flat_failed_still_fails_task(self):
        group = self._group('flat_fail', 'ANY', executors=[self.exec_a])
        task = self._flat_task(group)

        self._vote(task, self.exec_a, 'FAILED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        decision = TaskDecision.objects.get(task=task)
        self.assertEqual(decision.decision, TaskDecision.Decision.FAILED)

    # ------------------------------------------------- authorization (batch)

    def test_batch_vote_by_non_executor_rejected(self):
        # The batch shape branch must fall through to the same membership
        # check as the whole-task branch - a valid-shaped per-record vote is
        # not itself proof the caller may cast it.
        group1 = self._group('bauth1', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_batch_auth', (group1, None, None))
        task = self._batch_task(flow)

        with self.assertRaises(ValidationError):
            self._vote(task, self.outsider, {'ACCEPT': ['r1']})
        self.assertFalse(TaskDecision.objects.filter(task=task).exists())

    def test_batch_vote_by_privileged_resolver_allowed(self):
        group1 = self._group('bauth2', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_batch_priv', (group1, None, None))
        task = self._batch_task(flow)

        admin = create_test_interactive_user(username='flow_batch_admin')
        result = self._vote(task, admin, {'ACCEPT': ['r1']})
        self.assertTrue(result.get('success'), result)

    # --------------------------------------------- terminal transition guard

    def test_direct_terminal_status_update_rejected(self):
        # updateTask(status: COMPLETED) must not be a side door around
        # complete_task() - only step evaluation may close a flow task.
        group1 = self._group('tterm1', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_term', (group1, None, None))
        task = self._flow_task(flow)

        with self.assertRaises(ValidationError):
            TaskService(self.exec_a).update({'id': task.id, 'status': Task.Status.COMPLETED})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

    def test_reevaluate_after_completion_does_not_recomplete(self):
        # Stand-in for a true concurrency test (this codebase's tests run
        # single-threaded): re-entering evaluation on an already-terminal
        # task must be a no-op, which is exactly the property
        # _claim_terminal_transition exists to guarantee against a second
        # concurrent voter racing the same step to completion.
        from tasks_management.signals.on_task_resolve import re_evaluate_flow_task

        group1 = self._group('treev1', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_reeval_done', (group1, None, None))
        task = self._flow_task(flow)

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        version_after_completion = task.version

        re_evaluate_flow_task(task.id, self.exec_a)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        self.assertEqual(task.version, version_after_completion)


    # ------------------------------------------- review round 2 regressions

    def test_reviewer_in_two_pools_keeps_later_step_rejection(self):
        """
        business_status is deep-merged and its lists are CONCATENATED, so by
        the time the signal fires a reviewer who votes at two steps sees both
        steps' ids in one blob. Reading the verdict from there replayed step
        1's ACCEPT against step 2 and, because ACCEPT is processed first, the
        step-2 REJECT of the same record was dropped - silently flipping a
        rejection into an approval.
        """
        group1 = self._group('two_pool_1', 'ANY', executors=[self.exec_a])
        group2 = self._group('two_pool_2', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_two_pool', (group1, None, None), (group2, None, None))
        task = self._batch_task(flow)
        step1, step2 = flow.steps.order_by('order')

        self._vote(task, self.exec_a, {'ACCEPT': ['r1', 'r2']})
        task.refresh_from_db()
        self.assertEqual(task.current_step_id, step2.id)

        # Same reviewer, next step, now rejecting r1.
        self._vote(task, self.exec_a, {'REJECT': ['r1']})

        step2_decisions = {
            (d.record_id, d.decision)
            for d in TaskDecision.objects.filter(
                task=task, flow_step=step2, is_deleted=False)
        }
        self.assertIn(('r1', TaskDecision.Decision.REJECTED), step2_decisions)
        # step 1's ids must not be replayed against step 2
        self.assertNotIn(('r2', TaskDecision.Decision.APPROVED), step2_decisions)
        self.assertNotIn(('r1', TaskDecision.Decision.APPROVED), step2_decisions)

    def test_batch_vote_rejects_overlapping_record(self):
        group1 = self._group('overlap_g', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_overlap', (group1, None, None))
        task = self._batch_task(flow)

        with self.assertRaises(ValidationError):
            self._vote(task, self.exec_a, {'ACCEPT': ['r1'], 'REJECT': ['r1']})

    def test_batch_vote_rejects_non_list_records(self):
        group1 = self._group('nonlist_g', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_nonlist', (group1, None, None))
        task = self._batch_task(flow)

        # A bare string is iterable: without the shape check this recorded
        # one decision per character.
        with self.assertRaises(ValidationError):
            self._vote(task, self.exec_a, {'ACCEPT': 'r1'})

    def test_batch_vote_rejects_blank_record_id(self):
        group1 = self._group('blank_g', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_blank', (group1, None, None))
        task = self._batch_task(flow)

        with self.assertRaises(ValidationError):
            self._vote(task, self.exec_a, {'ACCEPT': ['r1', '  ']})

    def test_batch_decisions_get_history_rows(self):
        """
        Per-record decisions are bulk-inserted; a plain bulk_create emits no
        post_save, so simple-history would leave them with no historical row
        while whole-task decisions still got one.
        """
        group1 = self._group('hist_g', 'ANY', executors=[self.exec_a])
        flow = self._flow('f_hist', (group1, None, None))
        task = self._batch_task(flow)

        self._vote(task, self.exec_a, {'ACCEPT': ['r1', 'r2']})

        rows = TaskDecision.objects.filter(task=task, is_deleted=False)
        self.assertEqual(rows.count(), 2)
        for row in rows:
            self.assertEqual(row.history.count(), 1)

    def test_flat_n_group_waits_for_threshold(self):
        """
        resolve_task_n used to delegate to resolve_task_any, completing on the
        first approval and making TaskGroup.threshold a no-op setting.
        """
        group = self._group('flat_n', 'N', threshold=2, executors=[self.exec_a, self.exec_b])
        task = self._flat_task(group)

        self._vote(task, self.exec_a, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        self._vote(task, self.exec_b, 'APPROVED')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
