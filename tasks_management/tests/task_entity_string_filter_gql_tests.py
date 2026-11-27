"""
OP-3332: the `entityString_Icontains` task filter runs in SQL.

It used to load every visible task and its GenericForeignKey entity in Python, one
query (or more) per task, which took seconds on a few thousand tasks. It now adds,
for each entity type present among the tasks, a subquery on that type's table that
matches the entity's label, so the search costs the same whatever the task count.
"""
import json
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import User
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user
from tasks_management.models import Task, TaskGroup

SOURCE = "op3332_source"


class TaskEntityStringFilterGQLTestCase(openIMISGraphQLTestCase):
    admin_user = None
    admin_token = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = create_test_interactive_user(username="op3332Admin")
        cls.admin_token = BaseTestContext(user=cls.admin_user).get_jwt()
        # Entities of several types: a core User (a label of its own), a
        # TaskGroup (no __str__ of its own, so not searched), and none.
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

    def test_filter_is_case_insensitive(self):
        self.assertEqual(self._events("task", "ZEBRAfish"), ["op3332_user"])

    def test_history_filter_works(self):
        # taskHistory used to raise "'HistoricalTask' object has no attribute 'entity'".
        self.assertEqual(self._events("taskHistory", "ZEBRAfish"), ["op3332_user"])

    def test_filter_matches_each_entity_type(self):
        self.assertEqual(self._events("task", "okapi"), ["op3332_other_user"])
        self.assertEqual(self._events("task", "op3332"), ["op3332_other_user", "op3332_user"])

    def test_entity_without_own_str_is_not_searched(self):
        # Its label, "TaskGroup object (<id>)", is only its key.
        self.assertEqual(self._events("task", "taskgroup object"), [])
        self.assertEqual(self._events("task", str(self.group_entity.id)), [])
        self.assertEqual(self._events("taskHistory", "taskgroup object"), [])

    def test_task_without_entity_never_matches(self):
        # str(None) is "None": the Python filter used to match entity-less tasks
        # on any search contained in "none".
        self.assertEqual(self._events("task", "none"), [])
        self.assertEqual(self._events("taskHistory", "none"), [])

    def test_entity_string_output_still_resolves_live_entity(self):
        edges = self._query("task", "zebrafish")
        self.assertEqual(edges[0]["node"]["entityString"], str(self.user_entity))

    def test_renamed_entity_is_found_under_its_new_name(self):
        entity = create_test_interactive_user(username="op3332Before")
        self._create_task(entity, "op3332_rename")
        User.objects.filter(id=entity.id).update(username="op3332After")

        self.assertEqual(self._events("task", "op3332After"), ["op3332_rename"])
        self.assertEqual(self._events("task", "op3332Before"), [])

    def test_filter_query_count_does_not_grow_with_tasks(self):
        """The filter must not load each task's entity: same query count for 4 or 14 tasks."""
        self._query("task", "zzzz")  # warm the content type and user caches
        with CaptureQueriesContext(connection) as before:
            self._query("task", "zzzz")
        for i in range(10):
            entity = create_test_interactive_user(username=f"op3332Extra{i}")
            self._create_task(entity, "op3332_extra")
        with CaptureQueriesContext(connection) as after:
            self._query("task", "zzzz")
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))

    def test_type_without_label_expression_falls_back_to_str(self):
        """A model whose __str__ has no SQL counterpart is still searched, in Python, alone."""
        from tasks_management import entity_label

        labels = {k: v for k, v in entity_label.LABEL_EXPRESSIONS.items() if k != "core.user"}
        with mock.patch.object(entity_label, "LABEL_EXPRESSIONS", labels):
            self.assertEqual(self._events("task", "ZEBRAfish"), ["op3332_user"])
            self.assertEqual(self._events("taskHistory", "ZEBRAfish"), ["op3332_user"])
            self.assertEqual(self._events("task", "taskgroup object"), [])
            self.assertEqual(self._events("task", "none"), [])

    def test_user_label_matches_str(self):
        from tasks_management.entity_label import label_queryset

        for user in (self.user_entity, self.admin_user):
            labels = label_queryset(ContentType.objects.get_for_model(User)).filter(_pk=str(user.id))
            self.assertEqual(list(labels.values_list("_label", flat=True)), [str(user)])

    def test_every_label_expression_compiles(self):
        """A wrong field name in the map only shows when that type is searched: build them all."""
        from django.apps import apps
        from tasks_management.entity_label import LABEL_EXPRESSIONS, label_queryset

        for key in LABEL_EXPRESSIONS:
            try:
                model = apps.get_model(key)
            except LookupError:
                continue  # module not installed in this assembly
            with self.subTest(model=key):
                list(label_queryset(ContentType.objects.get_for_model(model))[:1])
