from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from tasks_management.apps import TasksManagementConfig
from tasks_management.models import Task, TaskExecutor, TaskGroup
from tasks_management.services import TaskService


class FlatVoteForgeryTestCase(TestCase):
    """
    OP-3322: a flat (ALL/ANY/N) task counts one verdict per executor, and an
    executor speaks for themselves only. Entries under another user's id in the
    submitted business_status are dropped, and the completion policies only
    count the keys of the group's current executors.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = create_test_interactive_user(username="forge_admin")
        cls.exec_a = create_test_interactive_user(username="forge_exec_a")
        cls.exec_b = create_test_interactive_user(username="forge_exec_b")
        cls.exec_c = create_test_interactive_user(username="forge_exec_c")

    def _group(self, code, policy, threshold=None, executors=()):
        group = TaskGroup(code=code, completion_policy=policy, threshold=threshold)
        group.save(username=self.admin.username)
        for user in executors:
            TaskExecutor(task_group=group, user=user).save(username=self.admin.username)
        return group

    def _task(self, group, business_status=None):
        task = Task(
            source='forge_source',
            status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_event='forge_event',
            business_status=business_status or {},
            data={},
            task_group=group,
        )
        task.save(username=self.admin.username)
        return task

    def _resolve(self, task, user, business_status):
        return TaskService(user).resolve_task({'id': task.id, 'business_status': business_status})

    def test_all_ignores_votes_forged_for_other_executors(self):
        group = self._group('forge_all', 'ALL', executors=[self.exec_a, self.exec_b])
        task = self._task(group)

        result = self._resolve(task, self.exec_a, {
            str(self.exec_a.id): 'APPROVED',
            str(self.exec_b.id): 'APPROVED',
        })
        self.assertTrue(result['success'], result)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertEqual(task.business_status, {str(self.exec_a.id): 'APPROVED'})

        self._resolve(task, self.exec_b, {str(self.exec_b.id): 'APPROVED'})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)

    def test_n_ignores_votes_forged_for_other_executors(self):
        group = self._group(
            'forge_n', 'N', threshold=2, executors=[self.exec_a, self.exec_b, self.exec_c])
        task = self._task(group)

        self._resolve(task, self.exec_a, {
            str(self.exec_a.id): 'APPROVED',
            str(self.exec_c.id): 'APPROVED',
        })
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

    def test_forged_failure_does_not_fail_the_task(self):
        group = self._group('forge_fail', 'ALL', executors=[self.exec_a, self.exec_b])
        task = self._task(group)

        self._resolve(task, self.exec_a, {
            str(self.exec_a.id): 'APPROVED',
            str(self.exec_b.id): 'FAILED',
        })
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

    def test_only_current_executors_are_counted(self):
        # Entries already stored under a non-executor key (a removed executor,
        # or one forged before this fix) no longer count towards completion.
        group = self._group('forge_stale', 'ALL', executors=[self.exec_a, self.exec_b])
        task = self._task(group, business_status={
            str(self.exec_c.id): 'APPROVED',
            'someone': 'APPROVED',
        })

        self._resolve(task, self.exec_a, {str(self.exec_a.id): 'APPROVED'})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        any_group = self._group('forge_stale_any', 'ANY', executors=[self.exec_a])
        any_task = self._task(any_group, business_status={str(self.exec_c.id): 'APPROVED'})
        self._resolve(any_task, self.exec_a, {str(self.exec_a.id): {'ACCEPT': [], 'REJECT': []}})
        any_task.refresh_from_db()
        self.assertEqual(any_task.status, Task.Status.ACCEPTED)

    def test_non_object_business_status_is_refused(self):
        group = self._group('forge_shape', 'ANY', executors=[self.exec_a])
        task = self._task(group)

        result = self._resolve(task, self.exec_a, 'APPROVED')
        self.assertFalse(result['success'])
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)
