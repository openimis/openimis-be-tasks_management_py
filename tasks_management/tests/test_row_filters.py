"""get_queryset honours the OP-2029 validity contract (epic OP-3317, phase 1).

Explicit validity values return the expected rows; a caller asking for
nothing gets what it got before the contract existed.
"""

from django.test import TestCase

from core.test_helpers import assert_row_filters, create_test_interactive_user, mark_deleted
from tasks_management.models import Task


class TaskRowFiltersTest(TestCase):
    def test_task(self):
        user = create_test_interactive_user(username="taskrowfilters")

        def task():
            row = Task(source="row_filters", business_event="row_filters", business_status={}, data={})
            row.save(username=user.username)
            return row

        live = task()
        deleted = mark_deleted(task())
        assert_row_filters(
            self, Task, Task.objects.filter(id__in=[live.id, deleted.id]), user,
            live.id, deleted.id, default_hides_deleted=True,
        )
