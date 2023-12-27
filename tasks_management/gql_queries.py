import json
import graphene
from uuid import UUID

from django.db.models import Q
from graphene_django import DjangoObjectType

from core import ExtendedConnection, prefix_filterset
from core.datetimes.ad_datetime import AdDatetime, AdDate
from core.gql_queries import UserGQLType
from core.models import HistoryModel
from core.services.utils import model_representation
from tasks_management.apps import TasksManagementConfig
from tasks_management.models import TaskGroup, TaskExecutor, Task

DICT_STRING = "{}"


def _convert_to_serializable_json(entity):
    converted_dict = {}

    model_fields = set(field.name for field in entity._meta.fields)
    history_fields = set(field.name for field in HistoryModel._meta.fields)

    fields_to_exclude = history_fields.intersection(model_fields)

    def convert_value(instance):
        if isinstance(instance, AdDatetime):
            return instance.strftime('%Y-%m-%d %H:%M:%S')
        elif isinstance(instance, AdDate):
            return instance.strftime('%Y-%m-%d')
        elif isinstance(instance, UUID):
            return str(instance)
        else:
            return instance

    for key, value in model_representation(entity).items():
        if key in fields_to_exclude:
            continue

        converted_dict[key] = convert_value(value)

    return json.dumps(converted_dict)


def is_task_triage(user):
    return user.has_perms(TasksManagementConfig.gql_task_group_create_perms
                          + TasksManagementConfig.gql_task_group_search_perms
                          + TasksManagementConfig.gql_task_group_update_perms
                          + TasksManagementConfig.gql_task_group_delete_perms)


class TaskGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    current_entity_data = graphene.JSONString()
    entity_string = graphene.String()

    class Meta:
        model = Task
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "entity_type": ["exact"],
            "entity_id": ["exact"],

            "source": ["exact", "iexact", "istartswith", "icontains"],
            "status": ["exact", "iexact", "istartswith", "icontains"],
            "executor_action_event": ["exact", "iexact", "istartswith", "icontains"],
            "business_event": ["exact", "iexact", "istartswith", "icontains"],

            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_current_entity_data(self, info):
        entity = self.entity
        if entity:
            serialized_json = _convert_to_serializable_json(entity)
            return serialized_json
        return DICT_STRING

    def resolve_entity_string(self, info):
        return self.entity.__str__()

    @classmethod
    def get_queryset(cls, queryset, info):
        user = info.context.user
        if user.is_imis_admin or is_task_triage(user):
            return queryset.filter(is_deleted=False)
        return queryset.filter(
            Q(task_group__taskexecutor__user=user) & ~Q(status=Task.Status.RECEIVED),
            is_deleted=False
        )


class TaskGroupGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    user = graphene.List(UserGQLType)

    class Meta:
        model = TaskGroup
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "code": ["exact", "iexact", "startswith", "istartswith", "contains", "icontains"],
            "completion_policy": ["exact", "iexact"],

            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_user(self, info):
        task_group_id = self.id
        return TaskExecutor.objects.filter(task_group_id=task_group_id)


class TaskExecutorGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    class Meta:
        model = TaskExecutor
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            **prefix_filterset("user__", UserGQLType._meta.filter_fields),
            **prefix_filterset("task_group__", TaskGroupGQLType._meta.filter_fields),
            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection
