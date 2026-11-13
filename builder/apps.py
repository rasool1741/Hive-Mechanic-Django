# -*- coding: utf-8 -*-

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BuilderConfig(AppConfig):
    name = 'builder'
    verbose_name = _('Hive Mechanic Game Builder')
    default_auto_field = 'django.db.models.AutoField'
