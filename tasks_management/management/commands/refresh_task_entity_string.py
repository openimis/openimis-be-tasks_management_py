from django.core.management.base import BaseCommand

from tasks_management.models import Task, refresh_entity_strings


class Command(BaseCommand):
    help = (
        "Recompute the searchable entity text (Task.entity_string) of every task and "
        "task history row from the current entities. A task stores that text when it "
        "is saved; run this after renaming entities in bulk, or if the 0014 backfill "
        "migration was skipped."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--skip-history", action="store_true", help="Leave the history rows as they are.")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        changed = refresh_entity_strings(Task._base_manager.all(), batch_size=batch_size)
        self.stdout.write(f"tasks: {changed} updated")
        if not options["skip_history"]:
            changed = refresh_entity_strings(Task.history.all(), batch_size=batch_size)
            self.stdout.write(f"task history: {changed} updated")
