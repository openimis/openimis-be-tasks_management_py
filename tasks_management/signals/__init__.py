from core.service_signals import ServiceSignalBindType
from core.signals import bind_service_signal
from tasks_management.signals.on_task_resolve import on_task_resolve, flow_rejected_record_ids


def bind_service_signals():
    bind_service_signal(
        'task_service.resolve_task',
        on_task_resolve,
        bind_type=ServiceSignalBindType.AFTER
    )


# Re-exported for consumer modules: a batch source applies its business action
# once at completion, to everything in the upload except the records rejected
# during the flow.
__all__ = ['bind_service_signals', 'on_task_resolve', 'flow_rejected_record_ids']
