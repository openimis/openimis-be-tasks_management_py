from django.db import migrations, models


PARTIAL_CHOICES = [
    ('RECEIVED', 'Received'),
    ('ACCEPTED', 'Accepted'),
    ('PARTIAL', 'Partial'),
    ('COMPLETED', 'Completed'),
    ('FAILED', 'Failed'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('tasks_management', '0012_remove_task_search_all_right'),
    ]

    operations = [
        migrations.AlterField(
            model_name='task',
            name='status',
            field=models.CharField(choices=PARTIAL_CHOICES, default='RECEIVED', max_length=255),
        ),
        migrations.AlterField(
            model_name='historicaltask',
            name='status',
            field=models.CharField(choices=PARTIAL_CHOICES, default='RECEIVED', max_length=255),
        ),
    ]
