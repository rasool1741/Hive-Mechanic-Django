from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('builder', '0052_player_user_link'),
    ]

    operations = [
        migrations.CreateModel(
            name='SessionEvent',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_id', models.CharField(max_length=128)),
                ('event_type', models.CharField(max_length=128)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('accepted', models.BooleanField(default=False)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='builder.session')),
            ],
            options={'ordering': ['created']},
        ),
        migrations.AddConstraint(
            model_name='sessionevent',
            constraint=models.UniqueConstraint(fields=('session', 'event_id'), name='builder_session_event_unique'),
        ),
    ]
