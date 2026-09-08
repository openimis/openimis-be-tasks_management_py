from django.core.management.base import BaseCommand, CommandError

from core.models import User
from tasks_management.models import Task
from tasks_management.signals.on_task_resolve import re_evaluate_flow_task


class Command(BaseCommand):
    help = (
        "Re-evaluate the current step of an in-review flow task without "
        "recording a vote. Use after refilling an emptied executor pool to "
        "resume a held task. Example: manage.py re_evaluate_task_step "
        "<task-uuid> --username Admin"
    )

    def add_arguments(self, parser):
        parser.add_argument('task_id', help='UUID of the flow task to re-evaluate')
        parser.add_argument(
            '--username', required=True,
            help='Acting user recorded on any resulting task transition',
        )

    def handle(self, *args, **options):
        user = User.objects.filter(username=options['username']).first()
        if not user:
            raise CommandError("User '%s' not found" % options['username'])
        task = Task.objects.filter(id=options['task_id'], is_deleted=False).first()
        if not task:
            raise CommandError("Task '%s' not found" % options['task_id'])
        if not task.flow_id:
            raise CommandError("Task '%s' is a flat task - nothing to re-evaluate" % task.id)

        re_evaluate_flow_task(task.id, user)
        task.refresh_from_db()
        step = task.current_step
        self.stdout.write(self.style.SUCCESS(
            "Task %s: status=%s, step order=%s (pool '%s')" % (
                task.id, task.status,
                step.order if step else None,
                step.task_group.code if step else None,
            )
        ))
