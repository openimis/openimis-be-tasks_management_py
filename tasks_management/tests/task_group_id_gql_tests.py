import json
from unittest import skipUnless

from django.apps import apps

from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user
from tasks_management.models import Task


@skipUnless(apps.is_installed("individual"), "groupId filters the individual module's groups")
class TaskGroupIdGQLTestCase(openIMISGraphQLTestCase):
    """
    OP-3274: groupId returns the tasks of a group, resolved on the server: tasks on the
    group itself and on any of its membership rows, including a removed member's.
    """
    admin_user = None
    admin_token = None
    group = None
    group_task = None
    member_task = None
    removed_member_task = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from individual.tests.test_helpers import (
            create_individual, create_group, add_individual_to_group,
        )
        cls.admin_user = create_test_interactive_user(username="taskGroupIdAdmin")
        cls.admin_token = BaseTestContext(user=cls.admin_user).get_jwt()
        username = cls.admin_user.username

        cls.group = create_group(username)
        member = add_individual_to_group(username, create_individual(username), cls.group)
        removed_member = add_individual_to_group(
            username, create_individual(username), cls.group, is_head=False)
        other_group = create_group(username)
        other_member = add_individual_to_group(username, create_individual(username), other_group)

        cls.group_task = cls._create_task(cls.group)
        cls.member_task = cls._create_task(member)
        cls.removed_member_task = cls._create_task(removed_member)
        cls._create_task(other_group)
        cls._create_task(other_member)
        cls._create_task(cls.admin_user)

        removed_member.delete(username=username)

    @classmethod
    def _create_task(cls, entity):
        task = Task(
            source='op3274_group_source',
            entity=entity,
            status=Task.Status.RECEIVED,
            business_event='op3274_group_event',
            business_status={},
            data={},
        )
        task.save(user=cls.admin_user)
        return task

    def _query_ids(self, query_name, args):
        response = self.query(
            f"""
            {{
              {query_name}(source: "op3274_group_source"{args}) {{
                edges {{ node {{ entityId }} }}
              }}
            }}
            """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        self.assertResponseNoErrors(response)
        content = json.loads(response.content)
        return sorted(edge["node"]["entityId"] for edge in content["data"][query_name]["edges"])

    def _expected(self):
        return sorted(t.entity_id for t in (self.group_task, self.member_task, self.removed_member_task))

    def test_task_with_group_id_returns_the_group_tasks(self):
        self.assertEqual(self._query_ids("task", f', groupId: "{self.group.id}"'), self._expected())

    def test_task_history_with_group_id_returns_the_group_tasks(self):
        ids = self._query_ids("taskHistory", f', groupId: "{self.group.id}"')
        self.assertEqual(sorted(set(ids)), self._expected())

    def test_task_with_unknown_group_id_returns_none(self):
        self.assertEqual(
            self._query_ids("task", ', groupId: "00000000-0000-0000-0000-000000000000"'), [])
