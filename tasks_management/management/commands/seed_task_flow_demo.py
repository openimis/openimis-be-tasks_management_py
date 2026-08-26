from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import User
from tasks_management.services import TaskFlowService, TaskGroupService


class Command(BaseCommand):
    help = (
        "Create a demo two-step approval flow bound to a task source: two "
        "task groups (one executor each) and an ANY->ANY flow. Example: "
        "manage.py seed_task_flow_demo --source BenefitPlanService "
        "--executors reviewer1,reviewer2 --username Admin"
    )

    def add_arguments(self, parser):
        parser.add_argument('--source', required=True,
                            help='Task source to bind the flow to (checker service class name)')
        parser.add_argument('--executors', required=True,
                            help='Two existing usernames, comma-separated: step1,step2')
        parser.add_argument('--username', required=True, help='Acting admin user')
        parser.add_argument('--code', default='DEMO_FLOW', help='Flow code (default DEMO_FLOW)')

    @transaction.atomic
    def handle(self, *args, **options):
        """
        Atomic end to end: a failure partway through (e.g. the second group's
        code already exists from a prior partial run) must not leave the
        first group committed - a retry would then fail again on that
        already-created group, needing manual cleanup before it can proceed.
        """
        admin = User.objects.filter(username=options['username']).first()
        if not admin:
            raise CommandError("User '%s' not found" % options['username'])
        executor_names = [u.strip() for u in options['executors'].split(',') if u.strip()]
        if len(executor_names) != 2:
            raise CommandError("--executors needs exactly two comma-separated usernames")
        executors = []
        for name in executor_names:
            user = User.objects.filter(username=name).first()
            if not user:
                raise CommandError("Executor user '%s' not found" % name)
            executors.append(user)

        code = options['code']
        group_service = TaskGroupService(admin)
        group_ids = []
        for idx, executor in enumerate(executors, start=1):
            result = group_service.create({
                'code': '%s_STEP%s' % (code, idx),
                'completion_policy': 'ANY',
                'user_ids': [str(executor.id)],
                'task_sources': [],
            })
            if not result.get('success'):
                raise CommandError("Failed to create group %s: %s" % (idx, result))
            group_ids.append(result['data']['id'])

        result = TaskFlowService(admin).create({
            'code': code,
            'name': '%s (demo)' % code,
            'task_sources': [options['source']],
            'steps': [
                {'task_group_id': group_ids[0], 'completion_policy': 'ANY', 'threshold': None},
                {'task_group_id': group_ids[1], 'completion_policy': 'ANY', 'threshold': None},
            ],
        })
        if not result.get('success'):
            raise CommandError("Failed to create flow: %s" % result)

        self.stdout.write(self.style.SUCCESS(
            "Flow '%s' created and bound to source '%s': step 1 -> %s (%s), "
            "step 2 -> %s (%s). New tasks from that source now route through "
            "the flow." % (
                code, options['source'],
                '%s_STEP1' % code, executor_names[0],
                '%s_STEP2' % code, executor_names[1],
            )
        ))
