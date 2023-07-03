from django.test import TestCase

from tasks_management.models import TaskGroup
from tasks_management.services import TaskGroupService
from tasks_management.tests.data import task_group_add_payload_any

from tasks_management.tests.helpers import LogInHelper


class TaskGroupServiceTest(TestCase):
    user = None
    task_executor = None
    service = None
    query_all = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.user = LogInHelper().get_or_create_user_api()
        cls.task_executor = LogInHelper().get_or_create_task_executor_api()
        cls.service = TaskGroupService(cls.user)
        cls.query_all = TaskGroup.objects.filter(is_deleted=False)
        cls.payload = {
            **task_group_add_payload_any,
            "user_ids": [cls.task_executor.id]
        }

    def test_add_task_group(self):
        result = self.service.create(self.payload)
        self.assertTrue(result.get('success', False), result.get('detail', "No details provided"))
        uuid = result.get('data', {}).get('uuid', None)
        query = self.query_all.filter(uuid=uuid)
        self.assertEqual(query.count(), 1)
