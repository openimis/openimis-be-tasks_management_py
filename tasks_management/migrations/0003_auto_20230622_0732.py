# Generated by Django 3.2.19 on 2023-06-22 07:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks_management', '0002_alter_task_content_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historicaltask',
            name='object_id',
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AlterField(
            model_name='task',
            name='object_id',
            field=models.PositiveIntegerField(null=True),
        ),
    ]
