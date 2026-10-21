from types import SimpleNamespace

from django.test import SimpleTestCase

from tasks_management.models import Task
from tasks_management.signals.on_task_resolve import (
    _mark_partial,
    _mark_user_done,
    cast_resolver_draft,
    explicit_resolution,
    store_resolver_draft,
    user_has_finished,
)


class _OpenTask:
    """Enough of a task for the per-user progress helpers. No database."""

    def __init__(self, status=Task.Status.ACCEPTED, progress=None):
        self.status = status
        self.json_ext = {'resolver_progress': dict(progress)} if progress else {}
        self.saves = 0

    def save(self, username=None):
        self.saves += 1
        self.saved_by = username


class ExplicitResolutionTests(SimpleTestCase):
    """Status on resolve is about the calling user. The policy still owns the task."""

    def test_omitted_status_keeps_a_whole_task_verdict_on_the_policy(self):
        self.assertIsNone(explicit_resolution(None, 'APPROVED'))
        self.assertIsNone(explicit_resolution(None, 'FAILED'))

    def test_omitted_status_keeps_accept_on_the_legacy_path(self):
        verdict = {'ACCEPT': ['row-1'], 'REJECT': ['row-2']}
        self.assertIsNone(explicit_resolution(None, verdict))
        self.assertIsNone(explicit_resolution(False, verdict))

    def test_done_finishes_the_user_and_does_not_close_the_task(self):
        verdict = {'ACCEPT': ['row-1']}
        self.assertEqual(explicit_resolution(Task.ResolveProgress.PARTIAL, verdict), 'partial')
        self.assertEqual(explicit_resolution(Task.ResolveProgress.DONE, 'APPROVED'), 'user_done')
        self.assertEqual(explicit_resolution('DONE', verdict), 'user_done')

    def test_an_unknown_status_does_not_change_the_outcome(self):
        self.assertIsNone(explicit_resolution('ACCEPTED', 'APPROVED'))
        self.assertIsNone(explicit_resolution('FAILED', {'ACCEPT': ['row-1']}))
        # COMPLETED is the task status, not a resolve flag.
        self.assertIsNone(explicit_resolution(Task.Status.COMPLETED, 'APPROVED'))

    def test_a_finished_user_cannot_update_and_a_partial_user_can(self):
        user = SimpleNamespace(id='user-1')
        other = SimpleNamespace(id='user-2')
        task = _OpenTask(progress={
            'user-1': Task.ResolveProgress.DONE,
            'user-2': Task.ResolveProgress.PARTIAL,
        })
        self.assertTrue(user_has_finished(task, user))
        self.assertFalse(user_has_finished(task, other))
        self.assertFalse(user_has_finished(_OpenTask(), user))

    def test_finishing_the_last_partial_user_leaves_the_task_accepted(self):
        user = SimpleNamespace(id='user-1', login_name='user-1')
        task = _OpenTask(status=Task.Status.PARTIAL, progress={'user-1': Task.ResolveProgress.PARTIAL})
        _mark_user_done(task, user)
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertTrue(user_has_finished(task, user))
        self.assertEqual(task.saves, 1)

    def test_partial_marks_the_task_under_work_and_casts_no_vote(self):
        user = SimpleNamespace(id='user-1', login_name='user-1')
        task = _OpenTask()
        task.business_status = {'user-1': {'ACCEPT': ['row-1']}}
        _mark_partial(task, user)
        self.assertEqual(task.status, Task.Status.PARTIAL)
        self.assertEqual(task.business_status, {'user-1': {'ACCEPT': ['row-1']}})
        self.assertFalse(user_has_finished(task, user))
        self.assertEqual(task.json_ext, {})

    def test_partial_saves_details_without_casting_them(self):
        user = SimpleNamespace(id='user-1', login_name='user-1')
        task = _OpenTask()
        task.business_status = {}
        store_resolver_draft(task, user, {'user-1': {'ACCEPT': ['row-1']}}, {'note': 'first'})
        store_resolver_draft(task, user, {'user-1': {'ACCEPT': ['row-1', 'row-2'], 'REJECT': ['row-3']}}, None)
        self.assertEqual(task.status, Task.Status.PARTIAL)
        self.assertEqual(task.business_status, {})
        self.assertFalse(user_has_finished(task, user))
        self.assertEqual(
            task.json_ext['resolver_draft']['business_status']['user-1'],
            {'ACCEPT': ['row-1', 'row-2'], 'REJECT': ['row-3']},
        )
        self.assertEqual(task.json_ext['resolver_draft']['additional_data']['user-1'], {'note': 'first'})

    def test_done_casts_the_saved_details_and_keeps_another_users_draft(self):
        user = SimpleNamespace(id='user-1', login_name='user-1')
        task = _OpenTask(status=Task.Status.PARTIAL)
        task.json_ext['resolver_draft'] = {
            'business_status': {
                'user-1': {'ACCEPT': ['row-1']},
                'user-2': {'ACCEPT': ['row-9']},
            },
            'additional_data': {'user-1': {'note': 'saved'}},
        }
        status, additional = cast_resolver_draft(
            task, user, {'user-1': {'REJECT': ['row-4']}}, None,
        )
        self.assertEqual(status['user-1'], {'ACCEPT': ['row-1'], 'REJECT': ['row-4']})
        self.assertEqual(additional, {'note': 'saved'})
        self.assertEqual(
            task.json_ext['resolver_draft']['business_status'],
            {'user-2': {'ACCEPT': ['row-9']}},
        )
        self.assertNotIn('user-1', task.json_ext['resolver_draft'].get('additional_data', {}))

    def test_done_ends_under_work_and_casts_the_vote(self):
        user = SimpleNamespace(id='user-1', login_name='user-1')
        task = _OpenTask(status=Task.Status.PARTIAL)
        _mark_user_done(task, user)
        self.assertEqual(task.status, Task.Status.ACCEPTED)
        self.assertTrue(user_has_finished(task, user))
        self.assertEqual(
            task.json_ext['resolver_progress']['user-1'],
            Task.ResolveProgress.DONE,
        )
