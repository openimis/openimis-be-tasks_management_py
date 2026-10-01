"""`task` / `taskHistory` need the task query right, then show only the user's tasks (audit C4)."""

from graphene import Schema
from graphene.test import Client

from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from tasks_management.apps import TasksManagementConfig
from tasks_management.models import Task, TaskExecutor, TaskGroup
from tasks_management.schema import Query

TASKS_QUERY = """
query { task(source: "c4_source") { edges { node { businessEvent } } } }
"""
TASK_HISTORY_QUERY = """
query { taskHistory(source: "c4_source") { edges { node { businessEvent } } } }
"""


class TaskRowSecurityTestCase(openIMISGraphQLTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        admin = create_test_interactive_user(username="c4_admin")
        role = create_test_role(perm_names=["gql_task_search_perms"], name="C4TaskSearchRole")
        cls.executor = create_test_interactive_user(username="c4_executor", roles=[role.id])
        cls.former_executor = create_test_interactive_user(username="c4_former", roles=[role.id])
        cls.outsider = create_test_interactive_user(username="c4_outsider", roles=[role.id])
        cls.no_right = create_test_interactive_user(username="c4_noright", roles=[])

        group = TaskGroup(code="c4_group", completion_policy="ANY")
        group.save(username=admin.username)
        TaskExecutor(task_group=group, user=cls.executor).save(username=admin.username)
        removed = TaskExecutor(task_group=group, user=cls.former_executor)
        removed.save(username=admin.username)
        removed.delete(username=admin.username)

        for event, status in (("c4_accepted", Task.Status.ACCEPTED), ("c4_received", Task.Status.RECEIVED)):
            Task(
                source="c4_source", status=status, business_event=event,
                executor_action_event=TasksManagementConfig.default_executor_event,
                business_status={}, data={}, task_group=group,
            ).save(username=admin.username)
        cls.gql_client = Client(Schema(query=Query))

    def _events(self, user, query=TASKS_QUERY):
        output = self.gql_client.execute(query, context=BaseTestContext(user).get_request())
        self.assertIsNone(output.get("errors"))
        field = next(iter(output["data"]))
        return {e["node"]["businessEvent"] for e in output["data"][field]["edges"]}

    def test_refused_without_task_query_right(self):
        for query in (TASKS_QUERY, TASK_HISTORY_QUERY):
            output = self.gql_client.execute(query, context=BaseTestContext(self.no_right).get_request())
            self.assertTrue(output.get("errors"))

    def test_executor_sees_own_group_tasks_once_accepted(self):
        self.assertEqual(self._events(self.executor), {"c4_accepted"})
        self.assertEqual(self._events(self.executor, TASK_HISTORY_QUERY), {"c4_accepted"})

    def test_removed_executor_and_outsider_see_nothing(self):
        self.assertEqual(self._events(self.former_executor), set())
        self.assertEqual(self._events(self.outsider), set())
        self.assertEqual(self._events(self.outsider, TASK_HISTORY_QUERY), set())
