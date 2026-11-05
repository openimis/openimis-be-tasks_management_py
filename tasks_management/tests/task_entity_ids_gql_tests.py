import json

from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user
from tasks_management.models import Task


class TaskEntityIdsGQLTestCase(openIMISGraphQLTestCase):
    """
    OP-3274: an explicitly empty entityIds list means "the tasks of no entity",
    not "no filter". Omitting the argument still returns every task.
    """
    admin_user = None
    admin_token = None
    entity_a = None
    entity_b = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = create_test_interactive_user(username="taskEntityIdsAdmin")
        cls.admin_token = BaseTestContext(user=cls.admin_user).get_jwt()
        cls.entity_a = create_test_interactive_user(username="taskEntityIdsA")
        cls.entity_b = create_test_interactive_user(username="taskEntityIdsB")
        cls._create_task(cls.entity_a)
        cls._create_task(cls.entity_b)

    @classmethod
    def _create_task(cls, entity):
        task = Task(
            source='op3274_source',
            entity=entity,
            status=Task.Status.RECEIVED,
            business_event='op3274_event',
            business_status={},
            data={},
        )
        task.save(user=cls.admin_user)
        return task

    def _query_entity_ids(self, query_name, entity_ids_arg):
        response = self.query(
            f"""
            {{
              {query_name}(source: "op3274_source"{entity_ids_arg}) {{
                edges {{ node {{ entityId }} }}
              }}
            }}
            """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        self.assertResponseNoErrors(response)
        content = json.loads(response.content)
        return sorted(edge["node"]["entityId"] for edge in content["data"][query_name]["edges"])

    def test_task_without_entity_ids_returns_all(self):
        self.assertEqual(
            self._query_entity_ids("task", ""),
            sorted([str(self.entity_a.id), str(self.entity_b.id)]),
        )

    def test_task_with_entity_ids_returns_matching(self):
        self.assertEqual(
            self._query_entity_ids("task", f', entityIds: ["{self.entity_a.id}"]'),
            [str(self.entity_a.id)],
        )

    def test_task_with_empty_entity_ids_returns_none(self):
        self.assertEqual(self._query_entity_ids("task", ", entityIds: []"), [])

    def test_task_history_with_empty_entity_ids_returns_none(self):
        self.assertEqual(self._query_entity_ids("taskHistory", ", entityIds: []"), [])
