import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ..models import City, Game, GameVersion, Player, Session

GRAPH = {
    'sequences': [{'id': 'legacy', 'items': [{'id': 'fallback'}]}],
    'initial-card': 'legacy#fallback',
    'graph': {
        'nodes': [
            {'id': 'riddle', 'title': 'The old gate', 'description': 'Find the year on the gate.', 'tools': [
                {'type': 'quiz', 'settings': {'question': 'Which year?', 'answer': '1921', 'score': 25}},
                {'type': 'hint', 'settings': {'content': 'Look up', 'score_cost': 5}},
            ]},
            {'id': 'finish', 'title': 'Done'},
        ],
        'edges': [{'id': 'riddle-finish', 'source': 'riddle', 'target': 'finish'}],
        'start': 'riddle', 'end': 'finish',
    },
}


class PlayerSessionApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='walker', password='secret')
        self.city = City.objects.create(slug='api-city', name='API City', center_latitude=35.7, center_longitude=51.4)
        self.game = Game.objects.create(name='Gate Hunt', slug='gate-hunt', city=self.city, status=Game.STATUS_PUBLISHED)
        self.version = GameVersion.objects.create(game=self.game, created='2026-01-01T00:00:00Z', definition=json.dumps(GRAPH))
        published, errors = self.version.publish()
        self.assertTrue(published, errors)
        self.game.status = Game.STATUS_PUBLISHED
        self.game.save()
        self.client.login(username='walker', password='secret')

    def start(self, slug='gate-hunt'):
        return self.client.post(reverse('player_start_session', args=[slug]))

    def test_start_creates_player_and_session_on_published_version(self):
        response = self.start()
        self.assertEqual(201, response.status_code)
        body = response.json()
        session = Session.objects.get(pk=body['session_id'])
        self.assertEqual(self.version, session.game_version)
        self.assertEqual(self.user, session.player.user)
        self.assertEqual('riddle', body['current_stage']['stage_id'])
        self.assertEqual('graph', body['runtime'])
        self.assertEqual(0, body['score'])

    def test_start_is_idempotent_while_a_session_is_ongoing(self):
        first = self.start().json()['session_id']
        second = self.start()
        self.assertEqual(200, second.status_code)
        self.assertEqual(first, second.json()['session_id'])
        self.assertEqual(1, Session.objects.count())
        self.assertEqual(1, Player.objects.filter(user=self.user).count())

    def test_state_never_leaks_answers_or_hint_text(self):
        session_id = self.start().json()['session_id']
        state = self.client.get(reverse('player_session_state', args=[session_id]))
        self.assertEqual(200, state.status_code)
        raw = json.dumps(state.json())
        self.assertNotIn('1921', raw)
        self.assertNotIn('Look up', raw)
        tools = {tool['type']: tool['settings'] for tool in state.json()['current_stage']['tools']}
        self.assertEqual({'question': 'Which year?'}, tools['quiz'])
        self.assertEqual({'score_cost': 5}, tools['hint'])

    def test_full_play_through_reaches_result(self):
        session_id = self.start().json()['session_id']
        action = reverse('game_action', args=[session_id])
        step = self.client.post(action, data=json.dumps({'event_id': 'e1', 'stage_id': 'riddle', 'answer': '1921'}), content_type='application/json')
        self.assertEqual(200, step.status_code, step.content)
        state = self.client.get(reverse('player_session_state', args=[session_id])).json()
        self.assertEqual('finish', state['current_stage']['stage_id'])
        self.assertTrue(state['current_stage']['is_end'])
        done = self.client.post(action, data=json.dumps({'event_id': 'e2', 'stage_id': 'finish'}), content_type='application/json')
        self.assertTrue(done.json()['completed'])
        state = self.client.get(reverse('player_session_state', args=[session_id])).json()
        self.assertIsNotNone(state['completed'])
        self.assertIsNone(state['current_stage'])
        self.assertGreater(state['score'], 0)
        again = self.start()
        self.assertEqual(201, again.status_code)
        self.assertNotEqual(session_id, again.json()['session_id'])

    def test_other_players_cannot_read_a_session(self):
        session_id = self.start().json()['session_id']
        get_user_model().objects.create_user(username='intruder', password='secret')
        self.client.logout()
        self.client.login(username='intruder', password='secret')
        self.assertEqual(404, self.client.get(reverse('player_session_state', args=[session_id])).status_code)
        self.assertEqual([], self.client.get(reverse('player_sessions')).json()['sessions'])

    def test_unplayable_games_are_refused(self):
        draft = Game.objects.create(name='Draft', slug='draft-game', city=self.city)
        GameVersion.objects.create(game=draft, created='2026-01-01T00:00:00Z', definition=json.dumps(GRAPH))
        self.assertEqual(404, self.start('draft-game').status_code)
        self.city.is_active = False
        self.city.save()
        self.assertEqual(404, self.start().status_code)
        self.assertEqual(0, Session.objects.count())

    def test_requires_sign_in_and_right_method(self):
        self.assertEqual(405, self.client.get(reverse('player_start_session', args=['gate-hunt'])).status_code)
        self.client.logout()
        self.assertEqual(302, self.start().status_code)
        self.assertEqual(0, Session.objects.count())

    def test_sessions_list_shows_own_sessions(self):
        self.start()
        sessions = self.client.get(reverse('player_sessions')).json()['sessions']
        self.assertEqual(1, len(sessions))
        self.assertEqual('gate-hunt', sessions[0]['game'])
