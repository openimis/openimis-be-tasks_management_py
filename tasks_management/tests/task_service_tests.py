import copy
from unittest import skip

from django.test import TestCase

from tasks_management.apps import TasksManagementConfig
from tasks_management.tests.data import TaskDataMixin
from tasks_management.services import TaskService, TaskGroupService
from tasks_management.models import Task
from core.test_helpers import LogInHelper
from django.db import connection


class TaskServiceTestCase(TestCase, TaskDataMixin):
    user = None
    task_executor = None
    service = None
    taskgroup_all = None
    taskgroup_any = None
    taskgroup_n = None

    @classmethod
    def setUpClass(cls):
        super(TaskServiceTestCase, cls).setUpClass()
        cls.user = LogInHelper().get_or_create_user_api()
        cls.task_executor = LogInHelper().get_or_create_user_api(
            username='TaskExecutor')
        cls.init_data()
        cls.taskgroup_all_id = cls.__create_taskgroup(cls.task_group_add_payload_all)
        cls.taskgroup_n_id = cls.__create_taskgroup(cls.task_group_add_payload_n)
        cls.taskgroup_any_id = cls.__create_taskgroup(cls.task_group_add_payload_any)
        cls.service = TaskService(cls.user)

    def test_create_task(self):
        if not connection.vendor == 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        result = self.service.create(self.task_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertGreater(len(Task.objects.filter(id=obj_id)), 0)
        self.assertEqual(Task.objects.filter(id=obj_id).first().status, Task.Status.RECEIVED)

    def test_update_task(self):
        if not connection.vendor == 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        result = self.service.create(self.task_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertGreater(len(Task.objects.filter(id=obj_id)), 0)

        update_payload = copy.deepcopy(self.task_payload)
        update_payload['source'] = 'updated_source'
        update_payload['id'] = result['data']['uuid']
        result = self.service.update(update_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])

    def test_delete_task(self):
        if not connection.vendor == 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        result = self.service.create(self.task_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertGreater(len(Task.objects.filter(id=obj_id)), 0)

        delete_payload = {'id': result['data']['uuid']}
        result = self.service.delete(delete_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])

    def test_complete_task(self):
        if not connection.vendor == 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        result = self.service.create(self.task_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertGreater(len(Task.objects.filter(id=obj_id)), 0)

        complete_payload = {'id': result['data']['uuid']}
        result = self.service.complete_task(complete_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        self.assertEqual(Task.objects.filter(id=obj_id).first().status, Task.Status.COMPLETED)

    def test_fail_task(self):
        if not connection.vendor == 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        result = self.service.create(self.task_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertGreater(len(Task.objects.filter(id=obj_id)), 0)

        complete_payload = {'id': result['data']['uuid'], 'failed': True}
        result = self.service.complete_task(complete_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        self.assertEqual(Task.objects.filter(id=obj_id).first().status, Task.Status.FAILED)

    @skip('To be redeveloped')
    def test_resolve_task_any(self):
        if not connection.vendor == 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        create_payload = {
            **self.task_payload_resolve_any,
            "task_group_id": self.taskgroup_any_id
        }
        result = self.service.create(create_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertGreater(len(Task.objects.filter(id=obj_id)), 0)

        resolve_payload = {"id": result["data"]["uuid"],
                           "business_status": {"Jan Kowalski": TasksManagementConfig.task_user_approved}}
        result = self.service.resolve_task(resolve_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        self.assertEqual(Task.objects.filter(id=obj_id).first().status, Task.Status.COMPLETED)

    @skip('To be redeveloped')
    def test_resolve_task_all(self):
        if not connection.vendor == 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        create_payload = {
            **self.task_payload_resolve_all,
            "task_group_id": self.taskgroup_any_id
        }
        result = self.service.create(create_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertGreater(len(Task.objects.filter(id=obj_id)), 0)

        resolve_payload = {"id": result["data"]["uuid"],
                           "business_status": {"Jan Kowalski": TasksManagementConfig.task_user_approved,
                                               "Adam Kowal": TasksManagementConfig.task_user_approved}}
        result = self.service.resolve_task(resolve_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        self.assertEqual(Task.objects.filter(id=obj_id).first().status, Task.Status.COMPLETED)

    @skip('To be redeveloped')
    def test_resolve_task_n(self):
        if not connection.vendor == 'mssql':
            self.skipTest("This test can only be executed for MSSQL database")
        create_payload = {
            **self.task_payload_resolve_n,
            "task_group_id": self.taskgroup_any_id
        }
        result = self.service.create(create_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        obj_id = result['data']['id']
        self.assertGreater(len(Task.objects.filter(id=obj_id)), 0)

        resolve_payload = {"id": result["data"]["uuid"],
                           "business_status": {"Jan Kowalski": TasksManagementConfig.task_user_approved}}
        result = self.service.resolve_task(resolve_payload)

        self.assertTrue(result)
        self.assertTrue(result['success'])
        self.assertEqual(Task.objects.filter(id=obj_id).first().status, Task.Status.COMPLETED)

    @classmethod
    def __create_taskgroup(cls, payload):
        object_data = {
            **payload,
            "user_ids": [str(cls.user.id), str(cls.task_executor.id)]
        }

        obj = TaskGroupService(cls.user).create(object_data)
        group_id = obj.get("data")["id"]

        return group_id
