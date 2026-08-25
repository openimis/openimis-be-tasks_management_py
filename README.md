# openIMIS Backend tasks_management reference module
This repository holds the files of the openIMIS Backend Task Managemet reference module.
It is dedicated to be deployed as a module of [openimis-be_py](https://github.com/openimis/openimis-be_py).

## ORM mapping:
* task_management_task, task_management_historicaltask > Task
* task_management_taskgroup, task_management_historicaltaskgroup > TaskGroup
* task_management_taskexecutor, task_management_historicaltaskexecutor > TaskExecutor
* task_management_taskflow, task_management_historicaltaskflow > TaskFlow
* task_management_taskflowstep, task_management_historicaltaskflowstep > TaskFlowStep
* task_management_taskdecision, task_management_historicaltaskdecision > TaskDecision

## GraphQl Queries
* task, taskGroup, taskExecutor, taskFlow, taskDecision, taskAssignmentTargets

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
* gql_task_search_all_perms: 191005
* gql_task_flow_search_perms: 192001
* gql_task_flow_create_perms: 192002
* gql_task_flow_update_perms: 192003
* gql_task_flow_delete_perms: 192004
* default_executor_event: default
* flow_ineligible_sources: import_valid_items, import_group_valid_items, claim_sampling

## openIMIS Modules Dependencies
- core

## Layered task approvals (approval flows)
A TaskFlow is a reusable, ordered sequence of TaskFlowSteps. Each step points at a
TaskGroup used as its executor pool, and may override that group's completion
policy (ALL / ANY / N+threshold) or inherit it. A task following a flow advances
step by step - `Task.flow` and `Task.current_step` track its position, and
`Task.task_group` is kept pointing at the active step's pool so existing
executor-based visibility and filtering keep working unchanged. Every vote is
recorded in TaskDecision, an insert-only ledger, before the step is evaluated.

A task reaches a flow one of two ways:
- **Automatically**, when its `source` is bound to a flow's `task_sources`
  (`TaskFlow.json_ext.task_sources`) - the flow is matched at task creation time,
  ahead of the legacy task-group source binding.
- **Manually**, via `updateTask(flowId: ...)` on an existing task - it is parked
  on the flow's first step, with its task group derived from that step's pool.
  `updateTask(detachFlow: true)` returns a task to a flat, group-only task.
  Both are refused once the task carries a TaskDecision, since decisions are
  recorded against the flow it was on.

TaskFlow is versioned via HistoryBusinessModel: editing a flow's steps replaces
it (a new head version is created, the old one is superseded), so tasks already
mid-review keep following the version they started on and only new tasks bind to
the new head.

**Role-rights (192001-192004) are not provisioned by a migration.** Like the
rest of this module's rights, `insert_role_right_for_system` is a deliberate
no-op - core assigns rights from the Roles administration UI at deployment
time, not from module code. A fresh deployment must grant these four rights to
the appropriate roles there before any approval-flow screen becomes usable to
anyone but a superuser.

**Not every task source can join a flow.** `flow_ineligible_sources` lists
sources whose resolution is per-record rather than per-task - CSV import
validators (`import_valid_items`, `import_group_valid_items`) and claim
sampling (`claim_sampling`) resolve accept/reject per row of a single task's
`business_status`, which the flow step model does not yet represent. A flow
match against one of these sources is skipped at creation, and a manual
`updateTask(flowId: ...)` against one of their tasks is rejected. Per-record
flow support is intentionally out of scope for this version.

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
