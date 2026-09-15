import random
import string
from datetime import date


def generate_task_group_code():
    """Generate a unique current-year plus five-digit TaskGroup code."""
    from tasks_management.models import TaskGroup
    year = date.today().strftime('%Y')
    while True:
        code = f"{year}{''.join(random.choices(string.digits, k=5))}"
        if not TaskGroup.objects.filter(code=code).exists():
            return code


def generate_task_flow_code():
    """Generate a unique current-year plus five-digit TaskFlow code."""
    from tasks_management.models import TaskFlow
    year = date.today().strftime('%Y')
    while True:
        code = f"{year}{''.join(random.choices(string.digits, k=5))}"
        if not TaskFlow.objects.filter(
            code=code, is_deleted=False, replacement_uuid__isnull=True,
        ).exists():
            return code
