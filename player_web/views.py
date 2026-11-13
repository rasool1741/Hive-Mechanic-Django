"""The player-facing Progressive Web App: a shell page plus sign-up and sign-in.

Game play itself happens in the browser against the JSON API in builder.views
(city catalogue, game detail, player sessions and game actions). Nothing here
grants builder access: builder views still require builder.builder_login.
"""

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

SERVICE_WORKER_VERSION = 'player-v2'

# (attempts, seconds) per client address; override in settings.
SIGNUP_LIMIT = (5, 60 * 60)
LOGIN_FAILURE_LIMIT = (10, 15 * 60)


def _signup_enabled():
    return getattr(settings, 'PLAYER_SIGNUP_ENABLED', True)


def _client_address(request):
    if getattr(settings, 'PLAYER_TRUST_X_FORWARDED_FOR', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _limit(name, default):
    return getattr(settings, name, default)


def _over_limit(kind, request, limit):
    count = cache.get('player_web:%s:%s' % (kind, _client_address(request)), 0)
    return count >= limit[0]


def _count(kind, request, limit):
    key = 'player_web:%s:%s' % (kind, _client_address(request))
    if cache.add(key, 1, limit[1]) is False:
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, limit[1])


def _too_many(request, mode):
    response = render(request, 'player_web/account.html', {
        'form': None, 'mode': mode, 'signup_enabled': _signup_enabled(),
        'blocked': 'تلاش‌ها بیش از حد مجاز بود. کمی بعد دوباره امتحان کنید.',
    })
    response.status_code = 429
    return response


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
        static('player_web/jsQR.js'),
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
    limit = _limit('PLAYER_SIGNUP_LIMIT', SIGNUP_LIMIT)
    if request.method == 'POST' and _over_limit('signup', request, limit):
        return _too_many(request, 'signup')
    form = UserCreationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        _count('signup', request, limit)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('player_app')
    return render(request, 'player_web/account.html', {'form': form, 'mode': 'signup', 'signup_enabled': True})


@require_http_methods(['GET', 'POST'])
def player_login(request):
    if request.user.is_authenticated:
        return redirect('player_app')
    limit = _limit('PLAYER_LOGIN_FAILURE_LIMIT', LOGIN_FAILURE_LIMIT)
    if request.method == 'POST' and _over_limit('login-failure', request, limit):
        return _too_many(request, 'login')
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            login(request, form.get_user())
            return redirect('player_app')
        _count('login-failure', request, limit)
    return render(request, 'player_web/account.html', {'form': form, 'mode': 'login', 'signup_enabled': _signup_enabled()})


@require_http_methods(['POST'])
def player_logout(request):
    logout(request)
    return redirect('player_login')
