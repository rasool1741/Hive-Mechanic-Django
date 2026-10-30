from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('builder', '0053_session_event')]

    operations = [
        migrations.CreateModel(
            name='Hint',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stage_id', models.CharField(max_length=255)),
                ('title', models.CharField(max_length=255)),
                ('content', models.TextField()),
                ('unlock_after_seconds', models.PositiveIntegerField(default=0)),
                ('unlock_after_attempts', models.PositiveIntegerField(default=0)),
                ('score_cost', models.IntegerField(default=0)),
                ('position', models.PositiveIntegerField(default=0)),
                ('enabled', models.BooleanField(default=True)),
                ('game_version', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='hints', to='builder.gameversion')),
            ],
            options={'ordering': ['stage_id', 'position']},
        ),
        migrations.CreateModel(
            name='ScoreLedger',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(max_length=255)),
                ('amount', models.IntegerField()),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('event', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='score_entries', to='builder.sessionevent')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='score_entries', to='builder.session')),
            ],
            options={'ordering': ['created']},
        ),
    ]
