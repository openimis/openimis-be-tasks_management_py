# Generated by Django 4.2.10 on 2024-06-03 12:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks_management', '0010_add_search_all_perms_admin'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicaltaskgroup',
            name='task_allowed_sources',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='taskgroup',
            name='task_allowed_sources',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
