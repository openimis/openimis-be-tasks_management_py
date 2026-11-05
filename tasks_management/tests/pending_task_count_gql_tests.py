import json

from django.apps import apps
from django.contrib.contenttypes.models import ContentType

from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase, BaseTestContext
from core.test_helpers import create_test_interactive_user, create_test_role
from tasks_management.models import Task
from tasks_management.validation import validate_existing_task


class PendingTaskCountGQLTestCase(openIMISGraphQLTestCase):
    """
    pendingTaskCount tells a user who may read an entity how many of its tasks would make
    validate_existing_task refuse an update, without needing any task right.
    """
    admin_user = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = create_test_interactive_user(username="pendingCountAdmin")
        benefit_plan_role = create_test_role(
            perm_names=["gql_benefit_plan_search_perms"], name="PendingCountBenefitPlanReader")
        individual_role = create_test_role(
            perm_names=["gql_individual_search_perms"], name="PendingCountIndividualReader")
        cls.reader = create_test_interactive_user(
            username="pendingCountReader", roles=[benefit_plan_role.id, individual_role.id])
        cls.no_right = create_test_interactive_user(username="pendingCountNoRight", roles=[])

        benefit_plan_model = apps.get_model("social_protection", "BenefitPlan")
        cls.benefit_plan_type = "social_protection.benefitplan"
        cls.pending_plan = cls._create_benefit_plan(benefit_plan_model, "PTC1")
        cls.free_plan = cls._create_benefit_plan(benefit_plan_model, "PTC2")
        cls.closed_plan = cls._create_benefit_plan(benefit_plan_model, "PTC3")
        cls._create_task(cls.pending_plan, Task.Status.RECEIVED)
        cls._create_task(cls.pending_plan, Task.Status.ACCEPTED)
        cls._create_task(cls.pending_plan, Task.Status.COMPLETED)
        cls._create_task(cls.closed_plan, Task.Status.COMPLETED)
        cls._create_task(cls.closed_plan, Task.Status.FAILED)

    @classmethod
    def _create_benefit_plan(cls, model, code):
        benefit_plan = model(
            code=code,
            name=f"Pending task count {code}",
            max_beneficiaries=0,
            ceiling_per_beneficiary="0.00",
            beneficiary_data_schema={},
            date_valid_from="2023-01-01",
            date_valid_to="2099-12-31",
        )
        benefit_plan.save(username=cls.admin_user.username)
        return benefit_plan

    @classmethod
    def _create_task(cls, entity, status):
        task = Task(
            source="pending_task_count_source",
            entity=entity,
            status=status,
            business_event="pending_task_count_event",
            business_status={},
            data={},
        )
        task.save(user=cls.admin_user)
        return task

    def _count(self, user, entity_type, entity_id):
        token = BaseTestContext(user=user).get_jwt()
        response = self.query(
            f'{{ pendingTaskCount(entityType: "{entity_type}", entityId: "{entity_id}") }}',
            headers={"HTTP_AUTHORIZATION": f"Bearer {token}"},
        )
        self.assertResponseNoErrors(response)
        return json.loads(response.content)["data"]["pendingTaskCount"]

    def _refusal(self, entity):
        return validate_existing_task({
            "entity_type": ContentType.objects.get_for_model(entity),
            "entity_id": entity.id,
        })

    def test_counts_only_received_and_accepted(self):
        self.assertEqual(self._count(self.admin_user, self.benefit_plan_type, self.pending_plan.id), 2)
        self.assertEqual(self._count(self.admin_user, self.benefit_plan_type, self.closed_plan.id), 0)

    def test_count_matches_the_refusal(self):
        for entity in (self.pending_plan, self.free_plan, self.closed_plan):
            count = self._count(self.admin_user, self.benefit_plan_type, entity.id)
            self.assertEqual(count > 0, bool(self._refusal(entity)), entity.code)

    def test_works_without_task_rights(self):
        self.assertEqual(self._count(self.reader, self.benefit_plan_type, self.pending_plan.id), 2)

    def test_null_without_the_entity_query_right(self):
        self.assertIsNone(self._count(self.no_right, self.benefit_plan_type, self.pending_plan.id))

    def test_null_for_an_entity_outside_the_user_scope(self):
        from location.test_helpers import create_test_village
        individual_model = apps.get_model("individual", "Individual")
        individual = individual_model(
            first_name="Pending", last_name="Scoped", dob="1980-01-01", location=create_test_village())
        individual.save(username=self.admin_user.username)
        self._create_task(individual, Task.Status.RECEIVED)

        self.assertEqual(self._count(self.admin_user, "individual.individual", individual.id), 1)
        self.assertIsNone(self._count(self.reader, "individual.individual", individual.id))

    def test_null_for_an_unknown_type_or_entity(self):
        self.assertIsNone(self._count(self.admin_user, "nomodule.nomodel", self.pending_plan.id))
        self.assertIsNone(self._count(self.admin_user, "benefitplan", self.pending_plan.id))
        self.assertIsNone(self._count(self.admin_user, self.benefit_plan_type, "not-a-uuid"))
        self.assertIsNone(
            self._count(self.admin_user, self.benefit_plan_type, "00000000-0000-0000-0000-000000000000"))
