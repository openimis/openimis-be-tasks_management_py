from django.apps import AppConfig

DEFAULT_CONFIG = {
    "gql_task_group_search_perms": ["190001"],
    "gql_task_group_create_perms": ["190002"],
    "gql_task_group_update_perms": ["190003"],
    "gql_task_group_delete_perms": ["190004"],
    
    "gql_task_search_perms": ["191001"],
    "gql_task_create_perms": ["191002"],
    "gql_task_update_perms": ["191003"],
    "gql_task_delete_perms": ["191004"],
    "gql_task_search_all_perms": ["191005"],

    "gql_task_flow_search_perms": ["192001"],
    "gql_task_flow_create_perms": ["192002"],
    "gql_task_flow_update_perms": ["192003"],
    "gql_task_flow_delete_perms": ["192004"],
    # To be used if task should use generic resolver
    "default_executor_event": "default",
    "task_user_approved": "APPROVED",
    # Sources whose tasks are resolved per-record by consumer modules at
    # resolve time (individual/social_protection imports, claim sampling).
    # Flows must not bind to them: their business logic executes on the
    # first vote, which would bypass every later step.
    "flow_ineligible_sources": [
        "import_valid_items",
        "import_group_valid_items",
        "claim_sampling",
    ],
}


class TasksManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tasks_management'

    gql_task_group_search_perms = None
    gql_task_group_create_perms = None
    gql_task_group_update_perms = None
    gql_task_group_delete_perms = None
    gql_task_search_perms = None
    gql_task_create_perms = None
    gql_task_update_perms = None
    gql_task_delete_perms = None
    gql_task_search_all_perms = None
    gql_task_flow_search_perms = None
    gql_task_flow_create_perms = None
    gql_task_flow_update_perms = None
    gql_task_flow_delete_perms = None
    default_executor_event = None
    task_user_approved = None
    flow_ineligible_sources = None

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
