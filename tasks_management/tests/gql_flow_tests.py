from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from tasks_management.apps import TasksManagementConfig
from tasks_management.gql_mutations import ResolveTaskMutation
from tasks_management.gql_queries import TaskDecisionGQLType
from tasks_management.models import Task, TaskDecision, TaskExecutor, TaskFlow, TaskFlowStep, TaskGroup


class GqlFlowTestCase(TestCase):
    """
    W4 coverage: resolveTask authorization (executor-or-triage), the flow
    perms role-rights migration, and TaskDecision query visibility scoping.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = create_test_interactive_user(username="gql_admin")
        cls.executor = create_test_interactive_user(username="gql_executor", roles=[2])
        cls.outsider = create_test_interactive_user(username="gql_outsider", roles=[2])

        cls.group = TaskGroup(code='gql_g1', completion_policy='ANY')
        cls.group.save(username=cls.admin.username)
        TaskExecutor(task_group=cls.group, user=cls.executor).save(username=cls.admin.username)

        cls.task = Task(
            source='gql_source', status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_status={}, data={}, business_event='gql_event',
            task_group=cls.group,
        )
        cls.task.save(username=cls.admin.username)

    def test_resolve_task_authorization(self):
        # executor of the task's current group: allowed
        ResolveTaskMutation._validate_mutation(
            self.executor, id=str(self.task.id), business_status='{}')
        # admin/triage: allowed
        ResolveTaskMutation._validate_mutation(
            self.admin, id=str(self.task.id), business_status='{}')
        # authenticated non-executor without triage rights: rejected
        with self.assertRaises(ValidationError):
            ResolveTaskMutation._validate_mutation(
                self.outsider, id=str(self.task.id), business_status='{}')

    def test_flow_mutations_require_perms(self):
        # Role-rights are provisioned outside migrations (core's
        # insert_role_right_for_system is a deliberate no-op) - deployments
        # grant 192001-192004 via the Roles administration UI, documented in
        # the README. Here we assert the enforcement side: a user without the
        # rights is rejected by every flow mutation.
        from tasks_management.gql_mutations import (
            CreateTaskFlowMutation,
            UpdateTaskFlowMutation,
            ReplaceTaskFlowMutation,
            DeleteTaskFlowMutation,
        )
        for mutation in (CreateTaskFlowMutation, UpdateTaskFlowMutation,
                         ReplaceTaskFlowMutation, DeleteTaskFlowMutation):
            with self.assertRaises(ValidationError, msg=mutation.__name__):
                mutation._validate_mutation(self.outsider)

    def test_task_decision_visibility_scoping(self):
        decision = TaskDecision(task=self.task, user=self.executor,
                                decision=TaskDecision.Decision.APPROVED)
        decision.save(username=self.admin.username)

        def visible_to(user):
            info = SimpleNamespace(context=SimpleNamespace(user=user))
            return TaskDecisionGQLType.get_queryset(TaskDecision.objects.all(), info)

        self.assertIn(decision, visible_to(self.admin))
        self.assertIn(decision, visible_to(self.executor))
        self.assertNotIn(decision, visible_to(self.outsider))

    # ------------------------------------------------- assignment targets

    def _targets(self, user, **kwargs):
        from tasks_management.schema import Query
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        return Query.resolve_task_assignment_targets(None, info, **kwargs)

    def test_assignment_targets_offer_only_assignable_flows(self):
        flow = TaskFlow(code='GQL_ASGN', name='assignable')
        flow.save(username=self.admin.username)
        TaskFlowStep(flow=flow, task_group=self.group, order=1).save(username=self.admin.username)

        stepless = TaskFlow(code='GQL_ASGN_EMPTY', name='no steps')
        stepless.save(username=self.admin.username)

        superseded = TaskFlow(code='GQL_ASGN_OLD', name='old')
        superseded.save(username=self.admin.username)
        TaskFlowStep(flow=superseded, task_group=self.group, order=1).save(
            username=self.admin.username)
        superseded.replacement_uuid = flow.id
        superseded.save(username=self.admin.username)

        targets = self._targets(self.admin)
        flows = {t.code: t for t in targets if t.kind == 'FLOW'}
        groups = {t.code: t for t in targets if t.kind == 'GROUP'}

        self.assertIn('GQL_ASGN', flows)
        self.assertEqual(flows['GQL_ASGN'].step_count, 1)
        # a flow with no steps, or a superseded version, would be refused by
        # the mutation - so the picker never shows them
        self.assertNotIn('GQL_ASGN_EMPTY', flows)
        self.assertNotIn('GQL_ASGN_OLD', flows)

        self.assertIn('gql_g1', groups)
        self.assertEqual(groups['gql_g1'].member_count, 1)
        self.assertEqual(groups['gql_g1'].completion_policy, 'ANY')

    def test_assignment_targets_filter_and_scope(self):
        flow = TaskFlow(code='GQL_SEARCHABLE', name='findme')
        flow.save(username=self.admin.username)
        TaskFlowStep(flow=flow, task_group=self.group, order=1).save(username=self.admin.username)

        by_code = self._targets(self.admin, search='SEARCHABLE')
        self.assertEqual([t.code for t in by_code], ['GQL_SEARCHABLE'])

        groups_only = self._targets(self.admin, include_flows=False)
        self.assertTrue(groups_only)
        self.assertTrue(all(t.kind == 'GROUP' for t in groups_only))

        with self.assertRaises(PermissionError):
            self._targets(self.outsider)

    def test_deleted_task_decisions_are_not_visible(self):
        """
        The task queryset hides deleted tasks; the decision ledger must not
        stay readable after its task is deleted, for privileged users either.
        """
        task = Task(
            source='gql_del_source', status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_status={}, data={}, business_event='gql_event',
            task_group=self.group,
        )
        task.save(username=self.admin.username)
        decision = TaskDecision(task=task, user=self.executor,
                                decision=TaskDecision.Decision.APPROVED)
        decision.save(username=self.admin.username)

        def visible_to(user):
            info = SimpleNamespace(context=SimpleNamespace(user=user))
            return TaskDecisionGQLType.get_queryset(TaskDecision.objects.all(), info)

        self.assertIn(decision, visible_to(self.admin))
        task.delete(username=self.admin.username)
        self.assertNotIn(decision, visible_to(self.admin))
        self.assertNotIn(decision, visible_to(self.executor))

    def test_delete_task_group_mutation_surfaces_service_refusal(self):
        """
        The service refuses to delete a group still used as a flow step pool.
        The mutation used to discard that result, reporting GraphQL success
        while nothing was deleted.
        """
        from tasks_management.gql_mutations import DeleteTaskGroupMutation

        pool = TaskGroup(code='gql_pool_in_use', completion_policy='ANY')
        pool.save(username=self.admin.username)
        flow = TaskFlow(code='GQL_POOL_FLOW', name='pool flow')
        flow.save(username=self.admin.username)
        TaskFlowStep(flow=flow, task_group=pool, order=1).save(username=self.admin.username)

        with self.assertRaises(ValidationError):
            DeleteTaskGroupMutation._mutate(self.admin, ids=[str(pool.id)])

        pool.refresh_from_db()
        self.assertFalse(pool.is_deleted)
