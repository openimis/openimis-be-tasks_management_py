from core.notifications import notify


def notify_task_assignees(task, event="assigned"):
    if not task.task_group_id:
        return []
    recipients = task.task_group.taskexecutor_set.filter(
        is_deleted=False,
        task_group__is_deleted=False,
    ).values_list("user_id", flat=True)
    return notify(
        recipients,
        f"task.{event}",
        f"tasksManagement.notification.{event}.title",
        f"tasksManagement.notification.{event}.message",
        message_params={
            "taskId": str(task.id),
            "source": task.source,
            "group": task.task_group.code,
            "status": task.status,
        },
        entity_type="tasks_management.task",
        entity_id=task.id,
        idempotency_key=f"task:{task.id}:{event}:{task.task_group_id}",
    )
