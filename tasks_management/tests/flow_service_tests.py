from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from core.test_helpers import create_test_interactive_user
from tasks_management.apps import TasksManagementConfig
from tasks_management.models import (
    Task,
    TaskExecutor,
    TaskFlow,
    TaskFlowStep,
    TaskGroup,
)
from tasks_management.services import TaskFlowService, TaskGroupService, TaskService


class FlowServiceTestCase(TestCase):
    """
    W3 coverage: TaskFlowService CRUD + custom atomic replace, source binding
    and eligibility gates, group-deletion guard, task assignment precedence,
    flow-task group reassignment guard, and the re_evaluate ops command.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = create_test_interactive_user(username="fs_admin")
        cls.exec_a = create_test_interactive_user(username="fs_exec_a")
        cls.exec_b = create_test_interactive_user(username="fs_exec_b")
        cls.service = TaskFlowService(cls.admin)

    def _group(self, code, policy='ANY', threshold=None, executors=None):
        group = TaskGroup(code=code, completion_policy=policy, threshold=threshold)
        group.save(username=self.admin.username)
        for user in (executors if executors is not None else [self.exec_a]):
            TaskExecutor(task_group=group, user=user).save(username=self.admin.username)
        return group

    def _flow_payload(self, code, group, **overrides):
        payload = {
            'code': code,
            'name': code,
            'task_sources': [],
            'steps': [{'task_group_id': str(group.id), 'completion_policy': 'ANY', 'threshold': None}],
        }
        payload.update(overrides)
        return payload

    def _create_flow(self, code, group, **overrides):
        result = self.service.create(self._flow_payload(code, group, **overrides))
        self.assertTrue(result.get('success'), result)
        return TaskFlow.objects.get(id=result['data']['id'])

    # ---------------------------------------------------------------- create

    def test_create_flow_with_steps_and_sources(self):
        group1 = self._group('fs_g1')
        group2 = self._group('fs_g2', executors=[self.exec_b])
        result = self.service.create({
            'code': 'FS_FLOW',
            'name': 'demo',
            'task_sources': ['FsSource'],
            'steps': [
                {'task_group_id': str(group1.id), 'completion_policy': None, 'threshold': None},
                {'task_group_id': str(group2.id), 'completion_policy': 'ALL', 'threshold': None},
            ],
        })
        self.assertTrue(result.get('success'), result)
        flow = TaskFlow.objects.get(id=result['data']['id'])
        self.assertEqual((flow.json_ext or {}).get('task_sources'), ['FsSource'])
        steps = list(flow.steps.filter(is_deleted=False).order_by('order'))
        self.assertEqual([s.order for s in steps], [1, 2])
        self.assertIsNone(steps[0].completion_policy)
        self.assertEqual(steps[1].completion_policy, 'ALL')

    def test_create_rejects_bad_payloads(self):
        group = self._group('fs_bad_g')
        empty_group = TaskGroup(code='fs_empty_g', completion_policy='ANY')
        empty_group.save(username=self.admin.username)

        cases = {
            'zero steps': self._flow_payload('FS_B1', group, steps=[]),
            'empty pool': self._flow_payload('FS_B2', empty_group),
            'N without threshold': self._flow_payload(
                'FS_B3', group,
                steps=[{'task_group_id': str(group.id), 'completion_policy': 'N', 'threshold': None}]),
            'threshold on ANY': self._flow_payload(
                'FS_B4', group,
                steps=[{'task_group_id': str(group.id), 'completion_policy': 'ANY', 'threshold': 2}]),
            'threshold above pool': self._flow_payload(
                'FS_B5', group,
                steps=[{'task_group_id': str(group.id), 'completion_policy': 'N', 'threshold': 5}]),
            'ineligible source': self._flow_payload(
                'FS_B6', group, task_sources=['import_valid_items']),
        }
        for label, payload in cases.items():
            result = self.service.create(payload)
            self.assertFalse(result.get('success'), f"{label} should fail: {result}")

    def test_create_rejects_duplicate_head_code_and_bound_source(self):
        group = self._group('fs_dup_g')
        self._create_flow('FS_DUP', group, task_sources=['FsDupSource'])

        dup_code = self.service.create(self._flow_payload('FS_DUP', group))
        self.assertFalse(dup_code.get('success'))

        dup_source = self.service.create(
            self._flow_payload('FS_DUP2', group, task_sources=['FsDupSource']))
        self.assertFalse(dup_source.get('success'))

        group_service = TaskGroupService(self.admin)
        group_bound = group_service.create({
            'code': 'fs_dup_g2', 'completion_policy': 'ANY',
            'user_ids': [str(self.exec_a.id)], 'task_sources': ['FsDupSource'],
        })
        self.assertFalse(group_bound.get('success'))

    # ---------------------------------------------------------------- update

    def test_update_name_and_sources_but_not_steps(self):
        group = self._group('fs_upd_g')
        flow = self._create_flow('FS_UPD', group)

        same_steps = self.service.update({
            'id': str(flow.id), 'code': 'FS_UPD', 'name': 'renamed',
            'task_sources': ['FsUpdSource'],
            'steps': [{'task_group_id': str(group.id), 'completion_policy': 'ANY', 'threshold': None}],
        })
        self.assertTrue(same_steps.get('success'), same_steps)
        flow.refresh_from_db()
        self.assertEqual(flow.name, 'renamed')
        self.assertEqual((flow.json_ext or {}).get('task_sources'), ['FsUpdSource'])

        changed_steps = self.service.update({
            'id': str(flow.id), 'code': 'FS_UPD',
            'steps': [{'task_group_id': str(group.id), 'completion_policy': 'ALL', 'threshold': None}],
        })
        self.assertFalse(changed_steps.get('success'))
        self.assertIn('replaceTaskFlow', str(changed_steps))

    # --------------------------------------------------------------- replace

    def test_replace_supersedes_head_and_recreates_steps(self):
        group1 = self._group('fs_rep_g1')
        group2 = self._group('fs_rep_g2', executors=[self.exec_b])
        flow = self._create_flow('FS_REP', group1, task_sources=['FsRepSource'])
        old_step = flow.steps.get()

        task = Task(
            source='FsRepSource', status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_status={}, data={},
            flow=flow, current_step=old_step, task_group=group1,
        )
        task.save(username=self.admin.username)

        result = self.service.replace({
            'id': str(flow.id),
            'steps': [
                {'task_group_id': str(group1.id), 'completion_policy': None, 'threshold': None},
                {'task_group_id': str(group2.id), 'completion_policy': None, 'threshold': None},
            ],
        })
        self.assertTrue(result.get('success'), result)

        flow.refresh_from_db()
        new_flow = TaskFlow.objects.get(id=result['uuid_new_object'])
        self.assertEqual(str(flow.replacement_uuid), str(new_flow.id))
        self.assertIsNotNone(flow.date_valid_to)
        self.assertEqual(new_flow.code, 'FS_REP')
        self.assertIsNone(new_flow.replacement_uuid)
        self.assertEqual(new_flow.steps.filter(is_deleted=False).count(), 2)
        # old version keeps its steps for pinned in-flight tasks
        self.assertEqual(flow.steps.filter(is_deleted=False).count(), 1)
        task.refresh_from_db()
        self.assertEqual(task.flow_id, flow.id)
        self.assertEqual(task.current_step_id, old_step.id)

        # replacing a superseded version is refused
        again = self.service.replace({'id': str(flow.id)})
        self.assertFalse(again.get('success'))

        # new tasks bind to the new head
        create_result = TaskService(self.admin).create({
            'source': 'FsRepSource', 'status': Task.Status.RECEIVED,
            'executor_action_event': TasksManagementConfig.default_executor_event,
            'business_status': {}, 'data': {}, 'business_event': 'x',
        })
        self.assertTrue(create_result.get('success'), create_result)
        new_task = Task.objects.get(id=create_result['data']['id'])
        self.assertEqual(new_task.flow_id, new_flow.id)

    # ---------------------------------------------------------------- delete

    def test_delete_blocked_by_in_flight_tasks_then_allowed(self):
        group = self._group('fs_del_g')
        flow = self._create_flow('FS_DEL', group)
        step = flow.steps.get()
        task = Task(
            source='FsDelSource', status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_status={}, data={},
            flow=flow, current_step=step, task_group=group,
        )
        task.save(username=self.admin.username)

        blocked = self.service.delete({'id': str(flow.id)})
        self.assertFalse(blocked.get('success'))

        task.status = Task.Status.COMPLETED
        task.save(username=self.admin.username)
        allowed = self.service.delete({'id': str(flow.id)})
        self.assertTrue(allowed.get('success'), allowed)
        flow.refresh_from_db()
        self.assertTrue(flow.is_deleted)
        self.assertFalse(flow.steps.filter(is_deleted=False).exists())

    def test_group_delete_blocked_while_head_step_pool(self):
        group = self._group('fs_gdel_g')
        self._create_flow('FS_GDEL', group)
        group_service = TaskGroupService(self.admin)

        blocked = group_service.delete({'id': str(group.id)})
        self.assertFalse(blocked.get('success'))
        group.refresh_from_db()
        self.assertFalse(group.is_deleted)

    # ------------------------------------------------------------ assignment

    def test_task_create_assigns_flow_and_gates(self):
        group = self._group('fs_asg_g')
        self._create_flow('FS_ASG', group, task_sources=['FsAsgSource'])
        service = TaskService(self.admin)

        base = {
            'status': Task.Status.RECEIVED, 'business_status': {}, 'data': {},
            'business_event': 'x',
            'executor_action_event': TasksManagementConfig.default_executor_event,
        }
        result = service.create({**base, 'source': 'FsAsgSource'})
        task = Task.objects.get(id=result['data']['id'])
        self.assertIsNotNone(task.flow_id)
        self.assertEqual(task.current_step.order, 1)
        self.assertEqual(task.task_group_id, group.id)
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        # non-default executor event never binds a flow
        result = service.create({**base, 'source': 'FsAsgSource',
                                 'executor_action_event': 'custom_event'})
        task = Task.objects.get(id=result['data']['id'])
        self.assertIsNone(task.flow_id)

    def test_flow_task_group_reassignment_rejected(self):
        group = self._group('fs_reas_g')
        other_group = self._group('fs_reas_g2', executors=[self.exec_b])
        flow = self._create_flow('FS_REAS', group)
        step = flow.steps.get()
        task = Task(
            source='FsReasSource', status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_status={}, data={},
            flow=flow, current_step=step, task_group=group,
        )
        task.save(username=self.admin.username)

        with self.assertRaises(ValidationError):
            TaskService(self.admin).update({
                'id': task.id, 'task_group_id': str(other_group.id),
            })

    # ----------------------------------------------------------- ops command

    def test_re_evaluate_command_resumes_held_task(self):
        # Step pool emptied after the flow was built -> task holds at step 1
        # even though its only member approved; refill + command resumes it.
        group = self._group('fs_cmd_g', policy='ALL', executors=[self.exec_a, self.exec_b])
        flow = self._create_flow(
            'FS_CMD', group,
            steps=[{'task_group_id': str(group.id), 'completion_policy': 'ALL', 'threshold': None}])
        step = flow.steps.get()
        task = Task(
            source='FsCmdSource', status=Task.Status.ACCEPTED,
            executor_action_event=TasksManagementConfig.default_executor_event,
            business_status={}, data={}, business_event='fs_cmd_event',
            flow=flow, current_step=step, task_group=group,
        )
        task.save(username=self.admin.username)

        TaskService(self.exec_a).resolve_task({
            'id': task.id, 'business_status': {str(self.exec_a.id): 'APPROVED'}})
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.ACCEPTED)

        TaskExecutor.objects.filter(task_group=group, user=self.exec_b).delete()
        call_command('re_evaluate_task_step', str(task.id), username=self.admin.username)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
