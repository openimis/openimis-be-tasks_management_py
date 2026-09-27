"""
Guard rails on tasks_management's rights declaration.

Same structure as `claim`, `product` and `contribution_plan`: `DJANGO_PERMS` by entity
then by action, `_PERM_CFG` deriving the config keys from it, and a `get_rights` on
each main model which is only an access point.

What is locked down here is the entity/action pair, not only the values:
  * an identifier in one place only (DJANGO_PERMS), hence no drift between the
    DEFAULT_CONFIG and the check;
  * a config key with no class attribute is never loaded by `__load_config` and
    reading it raises AttributeError - the right becomes unenforceable;
  * `has_perms([])` returns True, so an empty list grants to everybody.

The module carries three separate entities (190xxx / 191xxx / 192xxx): no identifier is
shared, and the test below verifies it. The sub-resources - membership of a group, flow
step, vote - have no rights of their own and delegate through `scope_parent`.
"""

import json
import os

from django.test import TestCase

from tasks_management.apps import (
    DJANGO_PERMS,
    TasksManagementConfig,
    _PERM_CFG,
    configured_perms,
    django_perms,
    perms,
)
from tasks_management.models import (
    Task,
    TaskDecision,
    TaskExecutor,
    TaskFlow,
    TaskFlowStep,
    TaskGroup,
)

# The identifiers as deployed (migrations 0002, 0005 and the baseline set of roles).
# Changing one is incompatible with the existing roles: this test has to be updated
# *and* the new right granted.
EXPECTED_RIGHTS = {
    "gql_task_group_search_perms": ["190001"],
    "gql_task_group_create_perms": ["190002"],
    "gql_task_group_update_perms": ["190003"],
    "gql_task_group_delete_perms": ["190004"],
    "gql_task_search_perms": ["191001"],
    "gql_task_create_perms": ["191002"],
    "gql_task_update_perms": ["191003"],
    "gql_task_delete_perms": ["191004"],
    "gql_task_flow_search_perms": ["192001"],
    "gql_task_flow_create_perms": ["192002"],
    "gql_task_flow_update_perms": ["192003"],
    "gql_task_flow_delete_perms": ["192004"],
}

# The `permissions_map.json` entries that carry these identifiers. The historical name
# in the openIMIS catalogue is not the django name declared in DJANGO_PERMS: what has to
# stay stable is the integer.
EXPECTED_MAP_ENTRIES = {
    "tasks_management.task_group_search": "190001",
    "tasks_management.task_group_create": "190002",
    "tasks_management.task_group_update": "190003",
    "tasks_management.task_group_delete": "190004",
    "tasks_management.task_search": "191001",
    "tasks_management.task_create": "191002",
    "tasks_management.task_update": "191003",
    "tasks_management.task_delete": "191004",
    "tasks_management.task_flow_search": "192001",
    "tasks_management.task_flow_create": "192002",
    "tasks_management.task_flow_update": "192003",
    "tasks_management.task_flow_delete": "192004",
}

# Keys declared but which no server call site reads. Kept because the identifiers are
# already granted to deployed roles (migration 0005_add_task_perms_to_admin) and read
# by the frontend; listed here so that adding a reader, or removing the key, is a
# visible decision.
DORMANT_KEYS = {
    "gql_task_create_perms",
    "gql_task_delete_perms",
}

# Withdrawn by 0012_remove_task_search_all_right: never enforced server-side.
# Reintroducing it would give back a right the migration takes away from the roles.
WITHDRAWN_RIGHT_ID = 191005

MODEL_BY_ENTITY = {
    "taskGroup": TaskGroup,
    "task": Task,
    "taskFlow": TaskFlow,
}

# Sub-resource -> the model whose rights it borrows.
SCOPE_PARENTS = {
    TaskExecutor: ("task_group", TaskGroup),
    TaskFlowStep: ("flow", TaskFlow),
    TaskDecision: ("task", Task),
}


def _permissions_map():
    """`permissions_map.json` lives in the assembly, not in the package."""
    from django.conf import settings

    candidates = [
        os.path.join(str(settings.BASE_DIR), "permissions_map.json"),
        os.path.join(os.path.dirname(str(settings.BASE_DIR)), "permissions_map.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as handle:
                return json.load(handle)
    return None


class TasksManagementPermissionDeclarationTestCase(TestCase):
    def test_right_ids_unchanged(self):
        self.assertEqual(
            {key: getattr(TasksManagementConfig, key) for key in EXPECTED_RIGHTS},
            EXPECTED_RIGHTS,
        )

    def test_perm_cfg_covers_every_declared_action(self):
        declared = {
            (entity, action)
            for entity, actions in DJANGO_PERMS.items()
            for action in actions
        }
        self.assertEqual(set(_PERM_CFG.values()), declared)

    def test_perm_cfg_matches_config_attributes(self):
        """`__load_config` ignores the keys with no class attribute."""
        missing = [key for key in _PERM_CFG if not hasattr(TasksManagementConfig, key)]
        self.assertEqual(missing, [])

    def test_no_right_list_is_empty(self):
        empty = [key for key in _PERM_CFG if not getattr(TasksManagementConfig, key)]
        self.assertEqual(empty, [])

    def test_attributes_carry_the_declared_right(self):
        for key, (entity, action) in _PERM_CFG.items():
            with self.subTest(key=key):
                self.assertEqual(
                    getattr(TasksManagementConfig, key), perms(entity, action)
                )

    def test_the_three_entities_share_no_right_id(self):
        """Three distinct business objects, three disjoint blocks of identifiers."""
        seen = {}
        for entity, actions in DJANGO_PERMS.items():
            for action, (_, right_id) in actions.items():
                seen.setdefault(right_id, []).append(f"{entity}.{action}")
        shared = {right: who for right, who in seen.items() if len(who) > 1}
        self.assertEqual(shared, {})

    def test_django_permission_names_are_unique(self):
        seen = {}
        for entity, actions in DJANGO_PERMS.items():
            for action, (name, _) in actions.items():
                seen.setdefault(name, []).append(f"{entity}.{action}")
        shared = {name: who for name, who in seen.items() if len(who) > 1}
        self.assertEqual(shared, {})

    def test_unknown_entity_or_action_raises(self):
        with self.assertRaises(KeyError):
            perms("nosuchentity", "query")
        with self.assertRaises(KeyError):
            perms("task", "nosuchaction")
        with self.assertRaises(KeyError):
            django_perms("taskFlow", "nosuchaction")

    def test_dormant_keys_are_still_declared(self):
        """
        Nobody reads them on the server side; they must carry their identifier all the
        same, and not [], otherwise the day a check does read them it will grant the
        action to everybody.
        """
        for key in DORMANT_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, _PERM_CFG)
                self.assertEqual(
                    getattr(TasksManagementConfig, key), EXPECTED_RIGHTS[key]
                )

    def test_withdrawn_search_all_right_is_not_reintroduced(self):
        """0012 takes 191005 away from the roles: redeclaring it would make it
        required again."""
        declared = {
            right_id
            for actions in DJANGO_PERMS.values()
            for _, right_id in actions.values()
        }
        self.assertNotIn(WITHDRAWN_RIGHT_ID, declared)

    def test_ids_match_permissions_map(self):
        mapping = _permissions_map()
        if mapping is None:
            self.skipTest("permissions_map.json absent de cet assemblage")
        actual = {name: mapping.get(name) for name in EXPECTED_MAP_ENTRIES}
        self.assertEqual(actual, EXPECTED_MAP_ENTRIES)

    # --- the access point through the model -------------------------------
    def test_each_model_exposes_every_action_of_its_entity(self):
        for entity, model in MODEL_BY_ENTITY.items():
            for action in DJANGO_PERMS[entity]:
                with self.subTest(entity=entity, action=action):
                    self.assertEqual(
                        model.get_rights(action), configured_perms(entity, action)
                    )
                    self.assertTrue(model.get_rights(action))

    def test_model_returns_none_for_an_undeclared_action(self):
        """None means "no rule": the caller must fail closed."""
        for model in MODEL_BY_ENTITY.values():
            with self.subTest(model=model.__name__):
                self.assertIsNone(model.get_rights("nosuchaction"))

    def test_resolving_a_task_is_not_a_declared_right(self):
        """
        Voting on a task is governed by membership of the executing group, not by a
        right: declaring a `resolve` action would amount to inventing one.
        """
        self.assertIsNone(Task.get_rights("resolve"))

    def test_model_reads_the_configured_value_not_the_declared_default(self):
        original = TasksManagementConfig.gql_task_search_perms
        try:
            TasksManagementConfig.gql_task_search_perms = ["999999"]
            self.assertEqual(Task.get_rights("query"), ["999999"])
            self.assertEqual(perms("task", "query"), ["191001"])
        finally:
            TasksManagementConfig.gql_task_search_perms = original

    # --- the sub-resources -------------------------------------------------
    def test_sub_resources_delegate_to_their_owner(self):
        """
        Each has several FKs and only one is the owner: the membership belongs to the
        group (not to the user), the step to the flow (not to the pool it references),
        the vote to the task (not to the step nor to the voter).
        """
        from core.rights_scope import scope_parent_of

        for model, (field, owner) in SCOPE_PARENTS.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model.scope_parent, field)
                self.assertIs(scope_parent_of(model), owner)
                self.assertFalse(hasattr(model, "get_rights"))

    def test_sub_resources_inherit_the_owner_rights(self):
        from core.rights_scope import model_rights

        for model, (_, owner) in SCOPE_PARENTS.items():
            for action in ("query", "create", "update", "delete"):
                with self.subTest(model=model.__name__, action=action):
                    self.assertEqual(
                        model_rights(model, action), owner.get_rights(action)
                    )
