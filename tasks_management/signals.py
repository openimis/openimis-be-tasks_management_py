from typing import Dict

from core.service_signals import ServiceSignalBindType
from core.signals import bind_service_signal
from tasks_management.models import TaskGroup, Task


def resolve_task_any(task, business_status: Dict[str, str]):
    if any(status == "APPROVED" for status in business_status.values()):
        return True
    return False


def resolve_task_all(task, business_status: Dict[str, str]):
    approve_count = sum(1 for status in business_status.values() if status == "APPROVED")
    group_members = TaskGroup.objects.get(task=task).taskexecutor_set.count()
    if approve_count == group_members:
        return True
    return False


def resolve_task_n(task, business_status: Dict[str, str]):
    approve_count = sum(1 for status in business_status.values() if status == "APPROVED")
    n = 1  # hardcoded for now
    if approve_count >= n:
        return True
    return False


def executor_action_event_handler(**kwargs):
    class_instance = kwargs.get("cls_")
    data = kwargs.get("result").get("data")
    task = Task.objects.get(id=data["task"]["id"])

    executor_action_event = task.executor_action_event
    business_status = task.business_status

    func = {
        "approve_all": resolve_task_all,
        "approve_any": resolve_task_any,
        "approve_n": resolve_task_n
    }.get(executor_action_event, None)

    if func:
        if func(task, business_status):
            class_instance.complete_task({"id": task.id})


def business_event_handler(**kwargs):
    data = kwargs.get("result").get("data")
    task = Task.objects.get(id=data["task"]["id"])
    business_event = task.business_event

    func = {}.get(business_event, None)

    if func:
        func(task.data)


def bind_service_signals():
    bind_service_signal(
        "task_service.resolve_task",
        executor_action_event_handler,
        bind_type=ServiceSignalBindType.AFTER
    )
    bind_service_signal(
        "task_service.complete_task",
        business_event_handler,
        bind_type=ServiceSignalBindType.AFTER
    )
