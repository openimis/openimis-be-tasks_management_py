from django.test import TestCase

from core.models import InteractiveUser
from core.test_helpers import LogInHelper
from tasks_management.gql_queries import TaskHistoryGQLType
from tasks_management.models import Task


class TaskHistoryEntityStringTestCase(TestCase):
    user = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()

    def _create_task(self, entity):
        task = Task(
            source='test_source',
            entity=entity,
            status=Task.Status.RECEIVED,
            business_event='test_business_event',
            business_status={'test': 'status'},
            data={},
        )
        task.save(user=self.user)
        return task

    def test_entity_string_resolves_entity_of_history_row(self):
        entity = InteractiveUser.objects.first()
        task = self._create_task(entity)
        history = Task.history.filter(id=task.id).latest('history_date')

        self.assertEqual(TaskHistoryGQLType.resolve_entity_string(history, None), str(entity))

    def test_entity_string_is_none_without_entity(self):
        task = self._create_task(None)
        history = Task.history.filter(id=task.id).latest('history_date')

        self.assertIsNone(TaskHistoryGQLType.resolve_entity_string(history, None))
