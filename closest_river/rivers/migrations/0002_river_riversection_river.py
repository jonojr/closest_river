# Generated by Django 5.0.10 on 2024-12-25 13:31

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rivers', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='River',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('osm_id', models.CharField(blank=True, max_length=1024, null=True)),
                ('name', models.CharField(max_length=1024)),
                ('destination', models.CharField(blank=True, default='', max_length=1024)),
                ('wikipedia', models.CharField(blank=True, default='', max_length=1024)),
                ('tags', models.JSONField(blank=True, default=dict)),
            ],
        ),
        migrations.AddField(
            model_name='riversection',
            name='river',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sections', to='rivers.river'),
        ),
    ]
