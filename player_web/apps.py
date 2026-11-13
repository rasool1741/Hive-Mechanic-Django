from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PlayerWebConfig(AppConfig):
    default_auto_field = 'django.db.models.AutoField'
    name = 'player_web'
    verbose_name = _('Player web app')
