import json
from uuid import UUID

import graphene
from graphene_django import DjangoObjectType

from core import ExtendedConnection, prefix_filterset
from core.datetimes.ad_datetime import AdDatetime
from core.gql_queries import UserGQLType
from core.models import HistoryModel
from core.services.utils import model_representation
from social_protection.models import BenefitPlan
from tasks_management.models import TaskGroup, TaskExecutor, Task


def _convert_to_serializable_json(entity):
    converted_dict = {}
    model_fields = set([field.name for field in entity._meta.fields])
    history_fields = set([field.name for field in HistoryModel._meta.fields])
    fields_to_exclude = history_fields.intersection(model_fields)

    for key, value in model_representation(entity).items():
        if key in fields_to_exclude:
            continue

        if isinstance(value, AdDatetime):
            ad_datetime_str = value.strftime('%Y-%m-%d %H:%M:%S')
            value = ad_datetime_str

        if isinstance(value, UUID):
            value = str(value)

        converted_dict[key] = value

    return json.dumps(converted_dict)


class TaskGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')
    current_entity_data = graphene.JSONString()

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
        entity = BenefitPlan.objects.first()
        if entity:
            serialized_json = _convert_to_serializable_json(entity)
            return serialized_json
        return {}


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
