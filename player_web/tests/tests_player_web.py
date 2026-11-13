import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from builder.models import City, Game, GameVersion, Session
from builder.tests.tests_player_session_api import GRAPH


class PlayerWebTests(TestCase):
    def setUp(self):
        cache.clear()

    def signup(self, client=None, username='newplayer'):
        return (client or self.client).post(reverse('player_signup'), {
            'username': username, 'password1': 'Walk-the-city-42', 'password2': 'Walk-the-city-42',
        })

    def test_app_shell_is_rtl_and_carries_manifest_and_csrf(self):
        response = self.client.get(reverse('player_app'))
        self.assertEqual(200, response.status_code)
        html = response.content.decode('utf-8')
        self.assertIn('dir="rtl"', html)
        self.assertIn(reverse('player_manifest'), html)
        self.assertIn('name="csrf-token"', html)
        self.assertIn('data-signed-in="0"', html)
        self.assertIn('csrftoken', response.cookies)

    def test_manifest_is_installable(self):
        response = self.client.get(reverse('player_manifest'))
        self.assertEqual('application/manifest+json', response['Content-Type'])
        manifest = json.loads(response.content)
        self.assertEqual('standalone', manifest['display'])
        self.assertEqual('/play/', manifest['start_url'])
        self.assertEqual({'192x192', '512x512'}, {icon['sizes'] for icon in manifest['icons']})
        for icon in manifest['icons']:
            self.assertTrue(icon['src'].endswith('.png'))

    def test_service_worker_is_scoped_and_never_caches_the_api(self):
        response = self.client.get(reverse('player_service_worker'))
        self.assertTrue(response['Content-Type'].startswith('application/javascript'))
        self.assertEqual('/play/', response['Service-Worker-Allowed'])
        body = response.content.decode('utf-8')
        self.assertIn("url.pathname.includes('/api/')", body)
        self.assertNotIn('&#x27;', body)
        self.assertIn('jsQR.js', body)

    def test_signup_logs_in_without_builder_access(self):
        response = self.signup()
        self.assertRedirects(response, reverse('player_app'), fetch_redirect_response=False)
        user = get_user_model().objects.get(username='newplayer')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.has_perm('builder.builder_login'))
        self.assertIn('data-signed-in="1"', self.client.get(reverse('player_app')).content.decode('utf-8'))
        self.assertEqual(403, self.client.get(reverse('builder_home')).status_code)

    def test_signup_rejects_mismatched_passwords(self):
        response = self.client.post(reverse('player_signup'), {'username': 'x1', 'password1': 'Walk-the-city-42', 'password2': 'other-42-pass'})
        self.assertEqual(200, response.status_code)
        self.assertFalse(get_user_model().objects.filter(username='x1').exists())

    @override_settings(PLAYER_SIGNUP_ENABLED=False)
    def test_signup_can_be_closed(self):
        self.assertEqual(404, self.client.get(reverse('player_signup')).status_code)
        self.assertEqual(404, self.signup().status_code)
        self.assertNotIn(reverse('player_signup'), self.client.get(reverse('player_login')).content.decode('utf-8'))

    def test_login_and_logout(self):
        get_user_model().objects.create_user(username='walker', password='Walk-the-city-42')
        response = self.client.post(reverse('player_login'), {'username': 'walker', 'password': 'Walk-the-city-42'})
        self.assertRedirects(response, reverse('player_app'), fetch_redirect_response=False)
        bad = Client().post(reverse('player_login'), {'username': 'walker', 'password': 'wrong'})
        self.assertEqual(200, bad.status_code)
        self.assertEqual(405, self.client.get(reverse('player_logout')).status_code)
        self.client.post(reverse('player_logout'))
        self.assertIn('data-signed-in="0"', self.client.get(reverse('player_app')).content.decode('utf-8'))

    def test_new_player_plays_a_game_with_csrf_enforced(self):
        city = City.objects.create(slug='web-city', name='Web City', center_latitude=35.7, center_longitude=51.4)
        game = Game.objects.create(name='Gate Hunt', slug='gate-hunt', city=city, status=Game.STATUS_PUBLISHED)
        version = GameVersion.objects.create(game=game, created=timezone.now(), definition=json.dumps(GRAPH))
        published, errors = version.publish()
        self.assertTrue(published, errors)

        client = Client(enforce_csrf_checks=True)
        client.get(reverse('player_signup'))
        signup = client.post(reverse('player_signup'), {
            'username': 'csrfplayer', 'password1': 'Walk-the-city-42', 'password2': 'Walk-the-city-42',
            'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
        })
        self.assertEqual(302, signup.status_code)
        client.get(reverse('player_app'))
        token = client.cookies['csrftoken'].value

        self.assertEqual(403, client.post(reverse('player_start_session', args=['gate-hunt'])).status_code)
        started = client.post(reverse('player_start_session', args=['gate-hunt']), HTTP_X_CSRFTOKEN=token)
        self.assertEqual(201, started.status_code, started.content)
        session_id = started.json()['session_id']
        action = reverse('game_action', args=[session_id])
        for event_id, stage, extra in (('w1', 'riddle', {'answer': '1921'}), ('w2', 'finish', {})):
            step = client.post(action, data=json.dumps(dict({'event_id': event_id, 'stage_id': stage}, **extra)),
                               content_type='application/json', HTTP_X_CSRFTOKEN=token)
            self.assertEqual(200, step.status_code, step.content)
        state = client.get(reverse('player_session_state', args=[session_id])).json()
        self.assertIsNotNone(state['completed'])
        self.assertIsNone(state['current_stage'])
        self.assertEqual(1, Session.objects.filter(player__user__username='csrfplayer').count())


    @override_settings(PLAYER_SIGNUP_LIMIT=(2, 3600))
    def test_signup_is_rate_limited_per_address(self):
        for index in range(2):
            self.assertEqual(302, self.signup(Client(), 'burst%d' % index).status_code)
        blocked = self.signup(Client(), 'burst9')
        self.assertEqual(429, blocked.status_code)
        self.assertFalse(get_user_model().objects.filter(username='burst9').exists())

    @override_settings(PLAYER_LOGIN_FAILURE_LIMIT=(3, 900))
    def test_failed_logins_are_rate_limited(self):
        get_user_model().objects.create_user(username='walker', password='Walk-the-city-42')
        for _ in range(3):
            self.assertEqual(200, self.client.post(reverse('player_login'), {'username': 'walker', 'password': 'nope'}).status_code)
        right = self.client.post(reverse('player_login'), {'username': 'walker', 'password': 'Walk-the-city-42'})
        self.assertEqual(429, right.status_code)

    @override_settings(GAME_WRONG_ANSWER_LIMIT=(3, 60))
    def test_wrong_answers_are_limited_per_stage(self):
        city = City.objects.create(slug='lim-city', name='Lim City', center_latitude=35.7, center_longitude=51.4)
        game = Game.objects.create(name='Gate Hunt', slug='gate-hunt', city=city, status=Game.STATUS_PUBLISHED)
        published, errors = GameVersion.objects.create(game=game, created=timezone.now(), definition=json.dumps(GRAPH)).publish()
        self.assertTrue(published, errors)
        get_user_model().objects.create_user(username='guesser', password='Walk-the-city-42')
        self.client.login(username='guesser', password='Walk-the-city-42')
        session_id = self.client.post(reverse('player_start_session', args=['gate-hunt'])).json()['session_id']
        action = reverse('game_action', args=[session_id])
        post = lambda event, answer: self.client.post(action, data=json.dumps({'event_id': event, 'stage_id': 'riddle', 'answer': answer}), content_type='application/json')
        for index in range(3):
            self.assertEqual(403, post('g%d' % index, str(index)).status_code)
        self.assertEqual(429, post('g-right', '1921').status_code)
        cache.clear()
        self.assertEqual(200, post('g-later', '1921').status_code)
