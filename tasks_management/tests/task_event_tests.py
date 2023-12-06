import copy
from unittest.mock import Mock

from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from tasks_management.tests.data import TaskDataMixin
from tasks_management.services import TaskService
from tasks_management.models import Task

from core.signals import REGISTERED_SERVICE_SIGNALS, bind_service_signal
from core.service_signals import RegisteredServiceSignal, ServiceSignalBindType

_signal_providing_args = ['cls_', 'data', 'context']
_signal_name_execute = 'task_service.execute_task'
_signal_name_complete = 'task_service.complete_task'


class TaskEventTestCase(TestCase, TaskDataMixin):
    user = None
    service = None
    mock_handler = None

    @classmethod
    def setUpClass(cls):
        super(TaskEventTestCase, cls).setUpClass()
        cls.user = create_test_interactive_user(username="test_admin")
        cls.service = TaskService(cls.user)
        cls.init_data()
        cls.mock_handler = Mock()
        REGISTERED_SERVICE_SIGNALS[_signal_name_execute] = RegisteredServiceSignal(_signal_providing_args)
        REGISTERED_SERVICE_SIGNALS[_signal_name_complete] = RegisteredServiceSignal(_signal_providing_args)
        bind_service_signal(_signal_name_execute, cls.mock_handler.execute, ServiceSignalBindType.AFTER)
        bind_service_signal(_signal_name_complete, cls.mock_handler.complete, ServiceSignalBindType.AFTER)

    def test_complete_task_event(self):
        result = self.service.create(self.task_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertTrue(Task.objects.filter(id=obj_id).exists())

        complete_payload = {'id': result['data']['uuid']}
        result = self.service.complete_task(complete_payload)
        self.mock_handler.complete.assert_called()
