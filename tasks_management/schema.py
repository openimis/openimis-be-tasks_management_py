import graphene
import json
import uuid
import graphene_django_optimizer as gql_optimizer

from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import CharField, Count, Q
from django.db.models.functions import Cast

from core.rights_scope import has_model_right
from core.schema import OrderedDjangoFilterConnectionField
from core.services import wait_for_mutation
from core.utils import append_validity_filter
from tasks_management.gql_mutations import CreateTaskGroupMutation, UpdateTaskGroupMutation, DeleteTaskGroupMutation, \
    UpdateTaskMutation, ResolveTaskMutation, CreateTaskFlowMutation, UpdateTaskFlowMutation, \
    ReplaceTaskFlowMutation, DeleteTaskFlowMutation
from tasks_management.gql_queries import TaskGroupGQLType, TaskExecutorGQLType, TaskGQLType, TaskHistoryGQLType, \
    TaskFlowGQLType, TaskDecisionGQLType, TaskAssignmentTargetGQLType
from tasks_management.models import TaskGroup, TaskExecutor, Task, TaskFlow, TaskDecision
from tasks_management.apps import TasksManagementConfig, require
from tasks_management.validation import pending_tasks


def group_tasks_filter(group_id):
    """
    The tasks of an individual-module group: those on the group itself and those on any
    of its membership rows. A removed member's row is only soft-deleted, so its tasks stay
    in the group's history. A malformed id, or no individual module, matches no task.
    """
    try:
        group_id = uuid.UUID(str(group_id))
        group_model = apps.get_model("individual", "Group")
        member_model = apps.get_model("individual", "GroupIndividual")
    except (ValueError, LookupError):
        return Q(pk__in=[])
    # A subquery rather than a list of ids, which MSSQL caps at 2100 parameters.
    member_ids = member_model.objects.filter(group_id=group_id).annotate(
        id_string=Cast("id", output_field=CharField())).values("id_string")
    on_group = Q(entity_type=ContentType.objects.get_for_model(group_model), entity_id=str(group_id))
    on_member = Q(entity_type=ContentType.objects.get_for_model(member_model), entity_id__in=member_ids)
    return on_group | on_member


class Query(graphene.ObjectType):
    module_name = "tasks_management"

    task_group = OrderedDjangoFilterConnectionField(
        TaskGroupGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        dateValidFrom__Gte=graphene.DateTime(),
        dateValidTo__Lte=graphene.DateTime(),
        client_mutation_id=graphene.String(),
        search=graphene.String(),
    )
    task_executor = OrderedDjangoFilterConnectionField(
        TaskExecutorGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        dateValidFrom__Gte=graphene.DateTime(),
        dateValidTo__Lte=graphene.DateTime(),
        client_mutation_id=graphene.String(),
        taskGroupIdString=graphene.String(),
    )

    task = OrderedDjangoFilterConnectionField(
        TaskGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        applyDefaultValidityFilter=graphene.Boolean(),
        client_mutation_id=graphene.String(),
        groupId=graphene.String(),
        customFilters=graphene.List(of_type=graphene.String),
        taskGroupId=graphene.String(),
        entityIds=graphene.List(graphene.UUID),
        entityString__Icontains=graphene.String(),
    )
    task_history = OrderedDjangoFilterConnectionField(
        TaskHistoryGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        applyDefaultValidityFilter=graphene.Boolean(),
        client_mutation_id=graphene.String(),
        groupId=graphene.String(),
        customFilters=graphene.List(of_type=graphene.String),
        taskGroupId=graphene.String(),
        entityIds=graphene.List(graphene.UUID),
        entityString__Icontains=graphene.String(),
    )
    task_flow = OrderedDjangoFilterConnectionField(
        TaskFlowGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        client_mutation_id=graphene.String(),
        search=graphene.String(),
        showSuperseded=graphene.Boolean(),
    )
    task_decision = OrderedDjangoFilterConnectionField(
        TaskDecisionGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        taskId=graphene.UUID(),
    )

    def resolve_task(self, info, **kwargs):
        user = info.context.user
        Query._require(user, "task", "query")
        filters = append_validity_filter(**kwargs)

        client_mutation_id = kwargs.get("client_mutation_id")
        if client_mutation_id:
            wait_for_mutation(client_mutation_id)
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        taskGroupId = kwargs.get("taskGroupId")
        if taskGroupId:
            filters.append(Q(task_group__id=taskGroupId))

        # An empty list means "the tasks of no entity", not "no filter".
        entityIds = kwargs.get("entityIds")
        if entityIds is not None:
            filters.append(Q(entity_id__in=entityIds))

        groupId = kwargs.get("groupId")
        if groupId:
            filters.append(group_tasks_filter(groupId))

        # Scoped here, not only by the type hook: the entityString filter below
        # walks the rows in Python, and must only see the ones the user may.
        query = Task.get_queryset(Task.objects.filter(*filters), user)

        entity_string = kwargs.get("entityString__Icontains")
        if entity_string:
            task_ids = [task.id for task in query if entity_string.lower() in str(task.entity).lower()]
            query = query.filter(id__in=task_ids)

        return gql_optimizer.query(query, info)

    def resolve_task_history(self, info, **kwargs):
        user = info.context.user
        Query._require(user, "task", "query")
        filters = append_validity_filter(**kwargs)

        client_mutation_id = kwargs.get("client_mutation_id")
        if client_mutation_id:
            wait_for_mutation(client_mutation_id)
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        taskGroupId = kwargs.get("taskGroupId")
        if taskGroupId:
            filters.append(Q(task_group__id=taskGroupId))

        # An empty list means "the tasks of no entity", not "no filter".
        entityIds = kwargs.get("entityIds")
        if entityIds is not None:
            filters.append(Q(entity_id__in=entityIds))

        groupId = kwargs.get("groupId")
        if groupId:
            filters.append(group_tasks_filter(groupId))

        # Scoped here, not only by the type hook: the entityString filter below
        # walks the rows in Python, and must only see the ones the user may.
        query = Task.get_queryset(Task.history.filter(*filters), user)

        entity_string = kwargs.get("entityString__Icontains")
        if entity_string:
            task_ids = [task.id for task in query if entity_string.lower() in str(task.entity).lower()]
            query = query.filter(id__in=task_ids)

        return gql_optimizer.query(query, info)

    task_assignment_targets = graphene.List(
        TaskAssignmentTargetGQLType,
        search=graphene.String(),
        first=graphene.Int(),
        include_flows=graphene.Boolean(),
        include_groups=graphene.Boolean(),
    )
    pending_task_count = graphene.Int(
        entity_type=graphene.String(required=True),
        entity_id=graphene.String(required=True),
    )

    def resolve_task_group(self, info, **kwargs):
        # Same right as resolve_task_executor just below: TaskGroupGQLType is this
        # module's only type without get_queryset nor ScopedQuerysetMixin, so nothing
        # restricted either the action or the rows.
        Query._check_permissions(
            info.context.user, TasksManagementConfig.gql_task_group_search_perms
        )
        filters = append_validity_filter(**kwargs)

        client_mutation_id = kwargs.get("client_mutation_id", None)
        if client_mutation_id:
            wait_for_mutation(client_mutation_id)
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        search = kwargs.get("search", None)
        if search is not None:
            filters.append(
                Q(code__icontains=search)
                | Q(taskexecutor__user__i_user__last_name__icontains=search)
                | Q(taskexecutor__user__i_user__other_names__icontains=search)
            )

        query = TaskGroup.objects.filter(*filters)
        return gql_optimizer.query(query, info)

    def resolve_task_executor(self, info, **kwargs):
        filters = append_validity_filter(**kwargs)

        client_mutation_id = kwargs.get("client_mutation_id")
        if client_mutation_id:
            wait_for_mutation(client_mutation_id)
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        task_group_id_string = kwargs.get("taskGroupIdString")
        if task_group_id_string:
            filters.append(Q(taskgroup__user__id_icontains=task_group_id_string))

        Query._check_permissions(info.context.user, TasksManagementConfig.gql_task_group_search_perms)
        query = TaskExecutor.objects.filter(*filters)
        return gql_optimizer.query(query, info)

    def resolve_task_flow(self, info, **kwargs):
        Query._check_permissions(info.context.user, TasksManagementConfig.gql_task_flow_search_perms)
        filters = []

        client_mutation_id = kwargs.get("client_mutation_id")
        if client_mutation_id:
            wait_for_mutation(client_mutation_id)

        # Head versions by default; showSuperseded exposes version history.
        if not kwargs.get("showSuperseded"):
            filters.append(Q(replacement_uuid__isnull=True))

        search = kwargs.get("search")
        if search is not None:
            filters.append(Q(code__icontains=search) | Q(name__icontains=search))

        query = TaskFlow.objects.filter(*filters, is_deleted=False)
        return gql_optimizer.query(query, info)

    def resolve_task_assignment_targets(
        self, info, search=None, first=20, include_flows=True, include_groups=True,
    ):
        """
        Serves the single "assigned to" picker. `first` caps each kind, not
        the combined list, so a long list of groups can never crowd the flows
        out of the dropdown.
        """
        Query._check_permissions(info.context.user, TasksManagementConfig.gql_task_search_perms)
        targets = []

        # Flows are only offered to users who may see them at all; everyone
        # else simply gets the groups, and the picker degrades to its old self.
        may_see_flows = info.context.user.has_perms(
            TasksManagementConfig.gql_task_flow_search_perms)
        if include_flows and may_see_flows:
            flows = TaskFlow.objects.filter(
                is_deleted=False, replacement_uuid__isnull=True,
            ).annotate(
                live_step_count=Count('steps', filter=Q(steps__is_deleted=False)),
            ).filter(live_step_count__gt=0)
            if search:
                flows = flows.filter(Q(code__icontains=search) | Q(name__icontains=search))
            targets += [
                TaskAssignmentTargetGQLType.from_flow(flow, flow.live_step_count)
                for flow in flows.order_by('code')[:first]
            ]

        if include_groups:
            groups = TaskGroup.objects.filter(is_deleted=False).annotate(
                live_member_count=Count(
                    'taskexecutor', filter=Q(taskexecutor__is_deleted=False)),
            )
            if search:
                groups = groups.filter(code__icontains=search)
            targets += [
                TaskAssignmentTargetGQLType.from_group(group, group.live_member_count)
                for group in groups.order_by('code')[:first]
            ]

        return targets

    def resolve_task_decision(self, info, **kwargs):
        # No perms check: TaskDecisionGQLType.get_queryset scopes rows to the
        # caller's task visibility, mirroring the task query.
        filters = []
        task_id = kwargs.get("taskId")
        if task_id:
            filters.append(Q(task__id=task_id))
        query = TaskDecision.objects.filter(*filters)
        return gql_optimizer.query(query, info)

    def resolve_pending_task_count(self, info, entity_type, entity_id):
        """
        How many tasks would make validate_existing_task refuse an update of the entity,
        `entity_type` being "app_label.model". Gated on reading the entity, not the task:
        Task.get_queryset hides RECEIVED tasks from most users, whose update is refused
        all the same, and the bare count tells no more than that refusal does.
        None, never an error, when the entity cannot be found or read.
        """
        user = info.context.user
        if type(user) is AnonymousUser or not user.id:
            return None
        try:
            app_label, model_name = entity_type.split(".")
            content_type = ContentType.objects.get_by_natural_key(app_label, model_name.lower())
        except (ValueError, ContentType.DoesNotExist):
            return None
        model = content_type.model_class()
        if model is None or not hasattr(model, "get_queryset") or not has_model_right(user, model, "query"):
            return None
        try:
            entity = model._default_manager.filter(pk=entity_id)
            if not model.get_queryset(entity, user).exists():
                return None
        except (ValueError, ValidationError):
            return None
        return pending_tasks(content_type, entity_id).count()

    @staticmethod
    def _check_permissions(user, perms):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(perms):
            raise PermissionError("Unauthorized")

    @staticmethod
    def _require(user, entity, action):
        if type(user) is AnonymousUser or not user.id or not require(user, entity, action):
            raise PermissionError("Unauthorized")


class Mutation(graphene.ObjectType):
    create_task_group = CreateTaskGroupMutation.Field()
    update_task_group = UpdateTaskGroupMutation.Field()
    delete_task_group = DeleteTaskGroupMutation.Field()

    update_task = UpdateTaskMutation.Field()
    resolve_task = ResolveTaskMutation.Field()

    create_task_flow = CreateTaskFlowMutation.Field()
    update_task_flow = UpdateTaskFlowMutation.Field()
    replace_task_flow = ReplaceTaskFlowMutation.Field()
    delete_task_flow = DeleteTaskFlowMutation.Field()
