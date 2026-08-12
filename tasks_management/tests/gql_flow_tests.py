from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from tasks_management.apps import TasksManagementConfig
from tasks_management.gql_mutations import ResolveTaskMutation
from tasks_management.gql_queries import TaskDecisionGQLType
from tasks_management.models import Task, TaskDecision, TaskExecutor, TaskGroup


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
