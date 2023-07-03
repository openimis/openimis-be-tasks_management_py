from core.models import InteractiveUser
from tasks_management.models import Task
from tasks_management.models import TaskGroup

task_group_add_payload_any = {
    "code": "example_any",
    "completion_policy": TaskGroup.TaskGroupCompletionPolicy.ANY,
}
task_group_add_payload_all = {
    "code": "example_all",
    "completion_policy": TaskGroup.TaskGroupCompletionPolicy.ALL,
}
task_group_add_payload_n = {
    "code": "example_n",
    "completion_policy": TaskGroup.TaskGroupCompletionPolicy.N,
}

task_payload = {
    'source': 'test_source',
    'entity': InteractiveUser.objects.first(),
    'status': Task.Status.RECEIVED,
    'executor_action_event': 'test_executor_action',
    'business_status': dict(),
    'business_event': 'test_business_action',
    'data': dict()
}

task_payload_resolve_any = {
    'source': 'test_source',
    'entity': InteractiveUser.objects.first(),
    'status': Task.Status.RECEIVED,
    'executor_action_event': 'approve_any',
    'business_status': dict(),
    'business_event': 'test_business_action_any',
    'data': dict()
}

task_payload_resolve_all = {
    'source': 'test_source',
    'entity': InteractiveUser.objects.first(),
    'status': Task.Status.RECEIVED,
    'executor_action_event': 'approve_all',
    'business_status': dict(),
    'business_event': 'test_business_action_all',
    'data': dict()
}

task_payload_resolve_n = {
    'source': 'test_source',
    'entity': InteractiveUser.objects.first(),
    'status': Task.Status.RECEIVED,
    'executor_action_event': 'approve_n',
    'business_status': dict(),
    'business_event': 'test_business_action_n',
    'data': dict()
}
