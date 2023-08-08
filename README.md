# openIMIS Backend tasks_management reference module
This repository holds the files of the openIMIS Backend Task Managemet reference module.
It is dedicated to be deployed as a module of [openimis-be_py](https://github.com/openimis/openimis-be_py).

## ORM mapping:
* task_management_task, task_management_historicaltask > Task
* task_management_taskgroup, task_management_historicaltaskgroup > TaskGroup
* task_management_taskexecutor, task_management_historicaltaskexecutor > TaskExecutor

## GraphQl Queries
* task, taskGroup, taskExecutor

## Services
- Task
  - create
  - update
  - delete
  - complete_task
  - resolve_task
- TaskGroup
  - create
  - update
  - delete
- TaskExecutor
  - create
  - update
  - delete
- CheckerLogicServiceMixin
  - create
  - update
  - delete
- on_task_complete_service_handler

## Configuration options (can be changed via core.ModuleConfiguration)
* gql_task_group_search_perms: 190001
* gql_task_group_create_perms: 190002
* gql_task_group_update_perms: 190003
* gql_task_group_delete_perms: 190004
* gql_task_search_perms: 191001
* gql_task_create_perms: 191002
* gql_task_update_perms: 191003
* gql_task_delete_perms: 191004
* default_executor_event: default

## openIMIS Modules Dependencies
- core

## Creating execution action handlers and business event handlers
When user action specified by the task is being passed to backend, the task service sends ``task_service.resolve_task`` 
signal. The approach for handler is to bind to ``after`` signal and check the specific ``executor_action_event`` of the 
task. The same approach is used for business event handlers, being required to bind on ``task_service.complete_task``.

```Python
# in signals.py in any module
def bind_service_signals():
    bind_service_signal(
        'task_service.resolve_task',
        handler_hook,
        bind_type=ServiceSignalBindType.AFTER
    )

def handler_hook(**kwargs):
    pass
```

## Creating tasks for BaseService implementations
CheckerLogicServiceMixin allows implementations of ``core.services.BaseService`` to generate tasks for create, update 
and delete actions. this adds create_<action>_task methods to the service, with the same API as the <action> methods.
Additionally the ``on_task_complete_service_handler`` service method allows to generate ``complete_task`` handlers for
``core.services.BaseService`` implementations. 

```Python
# In service definition
class ExampleService(BaseService, CheckerLogicServiceMixin):
    ...

# to create a task instead of performing create operation, instead of:
# ExampleService(user).create(data)
ExampleService(user).create_create_task(data)
`
#in signals.py (any module, but the same module as service preferred)
def bind_service_signals():
    bind_service_signal(
        'task_service.complete_task',
        on_task_complete_service_handler(ExampleService),
        bind_type=ServiceSignalBindType.AFTER
    )
```
