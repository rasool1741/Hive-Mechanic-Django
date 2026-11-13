# pylint: disable=line-too-long

from django.urls import re_path as url  # pylint: disable=no-name-in-module

from .views import player_app, player_manifest, player_service_worker, player_signup, player_login, player_logout

urlpatterns = [
    url(r'^manifest\.webmanifest$', player_manifest, name='player_manifest'),
    url(r'^sw\.js$', player_service_worker, name='player_service_worker'),
    url(r'^signup$', player_signup, name='player_signup'),
    url(r'^login$', player_login, name='player_login'),
    url(r'^logout$', player_logout, name='player_logout'),
    url(r'^$', player_app, name='player_app'),
]
