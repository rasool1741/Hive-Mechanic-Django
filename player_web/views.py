"""The player-facing Progressive Web App: a shell page plus sign-up and sign-in.

Game play itself happens in the browser against the JSON API in builder.views
(city catalogue, game detail, player sessions and game actions). Nothing here
grants builder access: builder views still require builder.builder_login.
"""

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

SERVICE_WORKER_VERSION = 'player-v1'


def _signup_enabled():
    return getattr(settings, 'PLAYER_SIGNUP_ENABLED', True)


@ensure_csrf_cookie
@never_cache
def player_app(request):
    return render(request, 'player_web/app.html', {
        'signed_in': request.user.is_authenticated,
        'username': request.user.get_username() if request.user.is_authenticated else '',
        'signup_enabled': _signup_enabled(),
    })


def player_manifest(request):
    icon = lambda size: {'src': static('player_web/icons/icon-%d.png' % size), 'sizes': '%dx%d' % (size, size), 'type': 'image/png', 'purpose': 'any maskable'}
    response = JsonResponse({
        'name': getattr(settings, 'PLAYER_APP_NAME', 'بازی شهری'),
        'short_name': getattr(settings, 'PLAYER_APP_SHORT_NAME', 'بازی شهری'),
        'lang': 'fa', 'dir': 'rtl',
        'start_url': reverse('player_app'),
        'scope': reverse('player_app'),
        'display': 'standalone',
        'background_color': '#0f172a',
        'theme_color': '#0f172a',
        'icons': [icon(192), icon(512)],
    }, json_dumps_params={'ensure_ascii': False})
    response['Content-Type'] = 'application/manifest+json'
    return response


@never_cache
def player_service_worker(request):
    shell = [
        reverse('player_app'),
        static('player_web/app.css'),
        static('player_web/app.js'),
        static('player_web/icons/icon-192.png'),
    ]
    body = render(request, 'player_web/sw.js', {'version': SERVICE_WORKER_VERSION, 'shell': shell}).content
    response = HttpResponse(body, content_type='application/javascript; charset=utf-8')
    response['Service-Worker-Allowed'] = reverse('player_app')
    return response


@require_http_methods(['GET', 'POST'])
def player_signup(request):
    if not _signup_enabled():
        raise Http404('Sign-up is closed.')
    if request.user.is_authenticated:
        return redirect('player_app')
    form = UserCreationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('player_app')
    return render(request, 'player_web/account.html', {'form': form, 'mode': 'signup', 'signup_enabled': True})


@require_http_methods(['GET', 'POST'])
def player_login(request):
    if request.user.is_authenticated:
        return redirect('player_app')
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        login(request, form.get_user())
        return redirect('player_app')
    return render(request, 'player_web/account.html', {'form': form, 'mode': 'login', 'signup_enabled': _signup_enabled()})


@require_http_methods(['POST'])
def player_logout(request):
    logout(request)
    return redirect('player_login')
