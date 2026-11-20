"""
OP-3332: the `entityString_Icontains` task filter runs in SQL.

It used to load every visible task and its GenericForeignKey entity in Python, one
query (or more) per task, which took seconds on a few thousand tasks. The text it
compares is now stored on the task (`Task.entity_string`), set from `str(entity)`
whenever the task is saved.
"""
import json

from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import User
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user
from tasks_management.models import Task, TaskGroup

SOURCE = "op3332_source"


class TaskEntityStringTestCase(openIMISGraphQLTestCase):
    admin_user = None
    admin_token = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = create_test_interactive_user(username="op3332Admin")
        cls.admin_token = BaseTestContext(user=cls.admin_user).get_jwt()
        # Entities of several types: a core User (whose str() reads its
        # sub-user, so it costs queries of its own), a TaskGroup, and none.
        cls.user_entity = create_test_interactive_user(username="op3332Zebrafish")
        cls.other_user_entity = create_test_interactive_user(username="op3332Okapi")
        cls.group_entity = TaskGroup(code="op3332_group", completion_policy="ANY")
        cls.group_entity.save(username=cls.admin_user.username)

        cls.user_task = cls._create_task(cls.user_entity, "op3332_user")
        cls.other_user_task = cls._create_task(cls.other_user_entity, "op3332_other_user")
        cls.group_task = cls._create_task(cls.group_entity, "op3332_group")
        cls.no_entity_task = cls._create_task(None, "op3332_none")

    @classmethod
    def _create_task(cls, entity, event):
        task = Task(
            source=SOURCE,
            entity=entity,
            status=Task.Status.RECEIVED,
            business_event=event,
            business_status={},
            data={},
        )
        task.save(user=cls.admin_user)
        return task

    def _query(self, query_name, search):
        response = self.query(
            f"""
            {{
              {query_name}(source: "{SOURCE}", entityString_Icontains: "{search}") {{
                edges {{ node {{ businessEvent entityString }} }}
              }}
            }}
            """,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        self.assertResponseNoErrors(response)
        content = json.loads(response.content)
        return content["data"][query_name]["edges"]

    def _events(self, query_name, search):
        return sorted({edge["node"]["businessEvent"] for edge in self._query(query_name, search)})

    def test_entity_string_is_stored_on_save(self):
        self.assertEqual(Task.objects.get(id=self.user_task.id).entity_string, str(self.user_entity))
        self.assertEqual(Task.objects.get(id=self.group_task.id).entity_string, str(self.group_entity))
        self.assertIsNone(Task.objects.get(id=self.no_entity_task.id).entity_string)

    def test_history_rows_carry_entity_string(self):
        history = Task.history.filter(id=self.user_task.id).latest("history_date")
        self.assertEqual(history.entity_string, str(self.user_entity))

    def test_filter_is_case_insensitive(self):
        self.assertEqual(self._events("task", "ZEBRAfish"), ["op3332_user"])
        self.assertEqual(self._events("taskHistory", "ZEBRAfish"), ["op3332_user"])

    def test_filter_matches_each_entity_type(self):
        self.assertEqual(self._events("task", "okapi"), ["op3332_other_user"])
        self.assertEqual(self._events("task", "taskgroup object"), ["op3332_group"])
        self.assertEqual(
            self._events("task", "op3332"), ["op3332_other_user", "op3332_user"]
        )

    def test_task_without_entity_never_matches(self):
        # str(None) is "None": the Python filter used to match entity-less tasks
        # on any search contained in "none".
        self.assertEqual(self._events("task", "none"), [])
        self.assertEqual(self._events("taskHistory", "none"), [])

    def test_entity_string_output_still_resolves_live_entity(self):
        edges = self._query("task", "zebrafish")
        self.assertEqual(edges[0]["node"]["entityString"], str(self.user_entity))

    def test_filter_query_count_does_not_grow_with_tasks(self):
        """The filter must not load each task's entity: same query count for 4 or 14 tasks."""
        with CaptureQueriesContext(connection) as before:
            self._query("task", "zzzz")
        for i in range(10):
            entity = create_test_interactive_user(username=f"op3332Extra{i}")
            self._create_task(entity, "op3332_extra")
        with CaptureQueriesContext(connection) as after:
            self._query("task", "zzzz")
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))

    def test_entity_string_is_refreshed_when_task_is_saved(self):
        entity = create_test_interactive_user(username="op3332Before")
        task = self._create_task(entity, "op3332_rename")
        User.objects.filter(id=entity.id).update(username="op3332After")

        # A snapshot: renaming the entity alone does not reach the task...
        self.assertEqual(self._events("task", "op3332Before"), ["op3332_rename"])

        # ...the next save of the task refreshes it.
        task = Task.objects.get(id=task.id)
        task.status = Task.Status.ACCEPTED
        task.save(user=self.admin_user)
        self.assertEqual(self._events("task", "op3332After"), ["op3332_rename"])
        self.assertEqual(self._events("task", "op3332Before"), [])

    def test_refresh_entity_strings_backfills_tasks_and_history(self):
        from tasks_management.models import refresh_entity_strings

        ids = [self.user_task.id, self.group_task.id, self.no_entity_task.id]
        Task.objects.filter(id__in=ids).update(entity_string="stale")
        Task.history.filter(id__in=ids).update(entity_string="stale")

        refresh_entity_strings(Task.objects.filter(id__in=ids), batch_size=2)
        refresh_entity_strings(Task.history.filter(id__in=ids), batch_size=2)

        stored = dict(Task.objects.filter(id__in=ids).values_list("id", "entity_string"))
        self.assertEqual(stored[self.user_task.id], str(self.user_entity))
        self.assertEqual(stored[self.group_task.id], str(self.group_entity))
        self.assertIsNone(stored[self.no_entity_task.id])
        self.assertFalse(Task.history.filter(id__in=ids, entity_string="stale").exists())

    def test_bulk_save_sets_entity_string(self):
        """HistoryModel.bulk_save goes through bulk_create/bulk_update, not save()."""
        from django.contrib.contenttypes.models import ContentType

        user_type = ContentType.objects.get_for_model(User)
        Task.bulk_save([{
            "source": "op3332_bulk", "entity_type": user_type, "entity_id": str(self.user_entity.id),
            "business_event": "op3332_bulk", "business_status": {}, "data": {},
        }], self.admin_user)
        created = Task.objects.get(source="op3332_bulk")
        self.assertEqual(created.entity_string, str(self.user_entity))

        Task.bulk_save([{"id": created.id, "entity_id": str(self.other_user_entity.id)}], self.admin_user)
        self.assertEqual(Task.objects.get(id=created.id).entity_string, str(self.other_user_entity))
        self.assertEqual(
            Task.history.filter(id=created.id).latest("history_date").entity_string, str(self.other_user_entity)
        )
