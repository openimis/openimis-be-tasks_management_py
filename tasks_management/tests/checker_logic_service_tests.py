from core.test_helpers import LogInHelper
from unittest.mock import MagicMock, patch
from django.test import TestCase
from tasks_management.services import BaseService, CheckerLogicServiceMixin
from tasks_management.models import Task

class CheckerLogicServiceTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()
        cls.service = ExampleService(cls.user)

    @patch('tasks_management.services.ContentType')
    def test_create_update_task(self, MockContentType):
        MockContentType.objects.get_for_model.return_value = None
        mock_object = MagicMock(
            id=1, first_name='Old', last_name='Doe',
            json_ext={'first_name': 'Old', 'last_name': 'Doe'}
        )
        self.service.OBJECT_TYPE.objects.get.return_value = mock_object

        # Mock input data with updated first_name and outdated json_ext
        obj_data = {
            'id': 1,
            'first_name': 'New',
            'last_name': 'Doe',
            'json_ext': {'first_name': 'Old', 'last_name': 'Doe'}
        }
        result = self.service.create_update_task(obj_data)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']

        self.assertTrue(Task.objects.filter(id=obj_id).exists())

        persisted_task = Task.objects.get(id=obj_id)

        expected_current_data = {
            'id': 1, 'first_name': 'Old', 'last_name': 'Doe',
            'json_ext': {'first_name': 'Old', 'last_name': 'Doe'}
        }
        self.assertEqual(persisted_task.data['current_data'], expected_current_data)

        expected_incoming_data = {
            'id': 1, 'first_name': 'New', 'last_name': 'Doe',
            'json_ext': {'first_name': 'New', 'last_name': 'Doe'}
        }
        self.assertEqual(persisted_task.data['incoming_data'], expected_incoming_data)


class ExampleService(BaseService, CheckerLogicServiceMixin):
    OBJECT_TYPE = MagicMock()
