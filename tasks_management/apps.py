from django.apps import AppConfig

from core.rights_declaration import RightsDeclaration

MODULE_NAME = "tasks_management"

# Rights, by entity then by action.
#
# Three business entities, three blocks of already deployed identifiers: `taskGroup` in
# 190xxx, `task` in 191xxx, `taskFlow` in 192xxx. No identifier is changed here.
#
# Notes on what is not obvious:
#
# * `task.create` (191002) and `task.delete` (191004) are **dormant declarations** on
#   the server side: no mutation and no resolver reads them. Tasks are not created
#   through GraphQL but by the consuming modules (`TaskService.create` called from a
#   signal), and nothing exposes their deletion. They are kept because they are granted
#   in the database (migration 0005_add_task_perms_to_admin, roles IMIS Administrator
#   and Task Triage) and read by the frontend
#   (SocialProtectionModule/src/constants.js: BENEFIT_PLAN_TASKS_CREATE / _DELETE):
#   removing them would make the existing roles lie and would hide buttons.
#
# * 191005 (`task_search_all`) is deliberately **not** declared: it was withdrawn by
#   migration 0012_remove_task_search_all_right ("never enforced server-side",
#   redundant with is_task_triage + 191001). Do not reintroduce it.
#
# * `replaceTaskFlow` has no action of its own: the mutation checks
#   `gql_task_flow_update_perms`, because replacing a flow is the versioned form of
#   modifying it. A distinct `replace` action would presuppose a distinct config key,
#   which does not exist.
#
# * `resolveTask` (an executor's vote) is governed by no `_perms` key at all: its check
#   is membership of the executing group, or triage/admin. So there is no `resolve`
#   action to declare here - that would be inventing a right.
DJANGO_PERMS = {
    "taskGroup": {
        "query": ("tasks_management.view_taskgroup", 190001),
        "create": ("tasks_management.add_taskgroup", 190002),
        "update": ("tasks_management.change_taskgroup", 190003),
        "delete": ("tasks_management.delete_taskgroup", 190004),
    },
    "task": {
        "query": ("tasks_management.view_task", 191001),
        # Dormant on the server side, see the note above.
        "create": ("tasks_management.add_task", 191002),
        "update": ("tasks_management.change_task", 191003),
        "delete": ("tasks_management.delete_task", 191004),
    },
    "taskFlow": {
        "query": ("tasks_management.view_taskflow", 192001),
        "create": ("tasks_management.add_taskflow", 192002),
        "update": ("tasks_management.change_taskflow", 192003),
        "delete": ("tasks_management.delete_taskflow", 192004),
    },
}

_PERM_CFG = {
    "gql_task_group_search_perms": ("taskGroup", "query"),
    "gql_task_group_create_perms": ("taskGroup", "create"),
    "gql_task_group_update_perms": ("taskGroup", "update"),
    "gql_task_group_delete_perms": ("taskGroup", "delete"),

    "gql_task_search_perms": ("task", "query"),
    "gql_task_create_perms": ("task", "create"),
    "gql_task_update_perms": ("task", "update"),
    "gql_task_delete_perms": ("task", "delete"),

    "gql_task_flow_search_perms": ("taskFlow", "query"),
    "gql_task_flow_create_perms": ("taskFlow", "create"),
    "gql_task_flow_update_perms": ("taskFlow", "update"),
    "gql_task_flow_delete_perms": ("taskFlow", "delete"),
}

RIGHTS = RightsDeclaration(MODULE_NAME, DJANGO_PERMS, _PERM_CFG)

perms = RIGHTS.perms
django_perms = RIGHTS.django_perm_names
configured_perms = RIGHTS.configured
require = RIGHTS.require


DEFAULT_CONFIG = {
    

    # To be used if task should use generic resolver
    "default_executor_event": "default",
    "task_user_approved": "APPROVED",
    # Sources whose tasks are resolved per-record by consumer modules at
    # resolve time. Flows must not bind to them: their business logic
    # executes on the first vote, which would bypass every later step.
    "flow_ineligible_sources": [
        "claim_sampling",
    ],
    # Sources that submit a per-record verdict ({ACCEPT: [...], REJECT: [...]})
    # instead of one verdict for the whole task. They follow flows through the
    # batch path: the batch advances as a unit, each step filters records out,
    # and the consumer module applies the surviving set once at completion.
    "flow_batch_sources": [
        "import_valid_items",
        "import_group_valid_items",
    ],
}


class TasksManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = MODULE_NAME

    # Rights: constants, no longer overridable. They go neither through DEFAULT_CFG
    # nor through ready(): `ModuleConfiguration.get_or_default` now ignores any
    # `_perms` key stored in the database.
    gql_task_group_search_perms = RIGHTS.perms("taskGroup", "query")
    gql_task_group_create_perms = RIGHTS.perms("taskGroup", "create")
    gql_task_group_update_perms = RIGHTS.perms("taskGroup", "update")
    gql_task_group_delete_perms = RIGHTS.perms("taskGroup", "delete")

    gql_task_search_perms = RIGHTS.perms("task", "query")
    gql_task_create_perms = RIGHTS.perms("task", "create")
    gql_task_update_perms = RIGHTS.perms("task", "update")
    gql_task_delete_perms = RIGHTS.perms("task", "delete")

    gql_task_flow_search_perms = RIGHTS.perms("taskFlow", "query")
    gql_task_flow_create_perms = RIGHTS.perms("taskFlow", "create")
    gql_task_flow_update_perms = RIGHTS.perms("taskFlow", "update")
    gql_task_flow_delete_perms = RIGHTS.perms("taskFlow", "delete")

    default_executor_event = None
    task_user_approved = None
    flow_ineligible_sources = None
    flow_batch_sources = None

    def ready(self):
        from core.models import ModuleConfiguration

        cfg = ModuleConfiguration.get_or_default(self.name, DEFAULT_CONFIG)
        self.__load_config(cfg)

    @classmethod
    def __load_config(cls, cfg):
        """
        Load all config fields that match current AppConfig class fields, all custom fields have to be loaded separately
        """
        for field in cfg:
            if hasattr(TasksManagementConfig, field):
                setattr(TasksManagementConfig, field, cfg[field])
