from typing import Dict

from core.service_signals import ServiceSignalBindType
from core.signals import bind_service_signal, REGISTERED_SERVICE_SIGNALS
from tasks_management.models import TaskGroup
from tasks_management.services import TaskService


def resolve_task_any(task, user, business_status: Dict[str, str], signal, sender):
    if any(status == "APPROVED" for status in business_status.values()):
        TaskService(user)._send_business_event(task)
        TaskService(user).complete_task({"id": task.id})


def resolve_task_all(task, user, business_status: Dict[str, str], signal, sender):
    approve_count = sum(1 for status in business_status.values() if status == "APPROVED")
    group_members = TaskGroup.objects.get(task=task).taskexecutor_set.count()
    if approve_count == group_members:
        TaskService(user)._send_business_event(task)
        TaskService(user).complete_task({"id": task.id})


def resolve_task_n(task, user, business_status: Dict[str, str], signal, sender):
    approve_count = sum(1 for status in business_status.values() if status == "APPROVED")
    n = 1  # hardcoded for now
    if approve_count >= n:
        TaskService(user)._send_business_event(task)
        TaskService(user).complete_task({"id": task.id})


def bind_service_signals():
    bind_service_signal(
        'resolve_task_any',
        resolve_task_any,
        bind_type=ServiceSignalBindType.BEFORE
    ),
    bind_service_signal(
        'resolve_task_all',
        resolve_task_all,
        bind_type=ServiceSignalBindType.BEFORE
    ),
    bind_service_signal(
        'resolve_task_n',
        resolve_task_n,
        bind_type=ServiceSignalBindType.BEFORE
    )
