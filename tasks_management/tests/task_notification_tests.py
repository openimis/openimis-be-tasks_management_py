from django.test import TestCase

from core.models import Notification
from core.test_helpers import create_test_interactive_user
from tasks_management.models import Task, TaskExecutor, TaskGroup
from tasks_management.services import TaskService


class TaskNotificationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = create_test_interactive_user(username="notification_admin")
        cls.group = TaskGroup(code="notification_group", completion_policy="ANY")
        cls.group.save(username=cls.admin.username)
        TaskExecutor(task_group=cls.group, user=cls.admin).save(
            username=cls.admin.username
        )

    def test_assigning_received_task_notifies_admin_in_group_once(self):
        task = Task(
            source="notification_source",
            status=Task.Status.RECEIVED,
            business_status={},
            data={},
        )
        task.save(username=self.admin.username)
        service = TaskService(self.admin)

        payload = {
            "id": task.id,
            "task_group_id": self.group.id,
            "status": Task.Status.ACCEPTED,
        }
        self.assertTrue(service.update(payload)["success"])
        self.assertTrue(service.update(payload)["success"])

        notification = Notification.objects.get(recipient=self.admin)
        self.assertEqual(notification.type, "task.assigned")
        self.assertEqual(notification.entity_id, str(task.id))
