import json
import time

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from ..models import City, Game, GameVersion, Hint, Player, ScoreLedger, Session, SessionEvent
from ..urban_tools import create_qr_token, inside_radius, validate_location_sample, verify_qr_token


class BackendGameFlowTests(TestCase):
    def test_graph_runtime_uses_server_selected_nodes(self):
        user = get_user_model().objects.create_user(username='graph-player', password='secret')
        city = City.objects.create(slug='graph-city', name='Graph City', center_latitude=35.7, center_longitude=51.4)
        game = Game.objects.create(name='Graph Game', slug='graph-game', city=city)
        definition = {
            'sequences': [{'id': 'legacy', 'items': [{'id': 'fallback'}]}],
            'initial-card': 'legacy#fallback',
            'graph': {
                'nodes': [
                    {'id': 'start', 'title': 'Start'},
                    {'id': 'finish', 'title': 'Finish'},
                ],
                'edges': [{'id': 'start-finish', 'source': 'start', 'target': 'finish'}],
                'start': 'start', 'end': 'finish',
            },
        }
        version = GameVersion.objects.create(game=game, created='2026-01-01T00:00:00Z', definition=json.dumps(definition))
        published, errors = version.publish()
        self.assertTrue(published, errors)
        player = Player.objects.create(identifier='user:graph-player', user=user)
        session = Session.objects.create(player=player, game_version=version, started='2026-01-01T00:00:00Z')
        self.client.login(username='graph-player', password='secret')
        endpoint = reverse('game_action', args=[session.pk])

        response = self.client.post(endpoint, data=json.dumps({'event_id': 'graph-1', 'stage_id': 'start'}), content_type='application/json')
        self.assertEqual(200, response.status_code)
        self.assertEqual('finish', response.json()['next_stage_id'])
        session.refresh_from_db()
        self.assertEqual('finish', session.session_state['graph_node_id'])
        self.assertEqual(409, self.client.post(endpoint, data=json.dumps({'event_id': 'graph-skip', 'stage_id': 'start'}), content_type='application/json').status_code)

        response = self.client.post(endpoint, data=json.dumps({'event_id': 'graph-2', 'stage_id': 'finish', 'score': 9999}), content_type='application/json')
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()['completed'])
        self.assertEqual(2, ScoreLedger.objects.filter(session=session).count())

    def test_server_authoritative_game_action_api(self):
        user = get_user_model().objects.create_user(username='api-player', password='secret')
        other_user = get_user_model().objects.create_user(username='other-player', password='secret')
        city = City.objects.create(slug='api-city', name='API City', center_latitude=35.7, center_longitude=51.4)
        game = Game.objects.create(name='API Game', slug='api-game', city=city)
        definition = {'sequences': [{'id': 'main', 'items': [{'id': 'one'}, {'id': 'two'}]}]}
        version = GameVersion.objects.create(game=game, created='2026-01-01T00:00:00Z', definition=json.dumps(definition))
        self.assertTrue(version.publish()[0])
        player = Player.objects.create(identifier='user:api-player', user=user)
        other_player = Player.objects.create(identifier='user:other-player', user=other_user)
        session = Session.objects.create(player=player, game_version=version, started='2026-01-01T00:00:00Z')
        other_session = Session.objects.create(player=other_player, game_version=version, started='2026-01-01T00:00:00Z')
        endpoint = reverse('game_action', args=[session.pk])

        self.assertEqual(302, self.client.post(endpoint, content_type='application/json').status_code)
        self.client.login(username='other-player', password='secret')
        self.assertEqual(404, self.client.post(endpoint, {'event_id': 'x', 'stage_id': 'main#one'}).status_code)
        self.client.login(username='api-player', password='secret')
        self.assertEqual(409, self.client.post(endpoint, data=json.dumps({
            'event_id': 'skip', 'stage_id': 'main#two', 'score': 9999,
        }), content_type='application/json').status_code)
        self.assertEqual(403, self.client.post(endpoint, data=json.dumps({
            'event_id': 'bad-gps', 'stage_id': 'main#one',
            'gps': {'latitude': 36.7, 'longitude': 52.4, 'accuracy': 5},
        }), content_type='application/json').status_code)
        response = self.client.post(endpoint, data=json.dumps({
            'event_id': 'valid-one', 'stage_id': 'main#one', 'score': 9999,
            'completed': True, 'next_stage_id': 'main#two',
        }), content_type='application/json')
        self.assertEqual(200, response.status_code)
        self.assertEqual(100, response.json()['score'])
        self.assertEqual('main#two', response.json()['next_stage_id'])
        self.assertEqual(1, SessionEvent.objects.filter(session=session).count())
        self.assertEqual(100, ScoreLedger.objects.get(session=session).amount)
        replay = self.client.post(endpoint, data=json.dumps({
            'event_id': 'valid-one', 'stage_id': 'main#one', 'score': 1,
        }), content_type='application/json')
        self.assertEqual(200, replay.status_code)
        self.assertEqual(1, SessionEvent.objects.filter(session=session).count())
        final = self.client.post(endpoint, data=json.dumps({'event_id': 'valid-two', 'stage_id': 'main#two'}), content_type='application/json')
        self.assertEqual(True, final.json()['completed'])
        self.assertIsNotNone(Session.objects.get(pk=session.pk).completed)
        self.assertEqual(409, self.client.post(endpoint, data=json.dumps({'event_id': 'after', 'stage_id': 'main#two'}), content_type='application/json').status_code)
        self.assertEqual(404, other_session.game_version_id and self.client.post(reverse('game_action', args=[other_session.pk]), data=json.dumps({'event_id': 'x', 'stage_id': 'main#one'}), content_type='application/json').status_code)

    def test_complete_server_side_game_flow(self):
        user = get_user_model().objects.create_user(username='e2e-player', password='secret')
        city = City.objects.create(
            slug='e2e-city', name='E2E City', center_latitude=35.7,
            center_longitude=51.4, is_active=True,
        )
        game = Game.objects.create(name='E2E Game', slug='e2e-game', city=city)
        version = GameVersion.objects.create(
            game=game,
            created='2026-01-01T00:00:00Z',
            definition=json.dumps({
                'sequences': [{'id': 'main', 'items': [{'id': 'start'}]}],
                'initial-card': 'main#start',
            }),
        )

        published, errors = version.publish()
        self.assertTrue(published)
        self.assertEqual([], errors)

        player = Player.objects.create(identifier='user:e2e-player', user=user)
        session = Session.objects.create(player=player, game_version=version, started='2026-01-01T00:00:00Z')

        self.assertEqual([], validate_location_sample(35.7, 51.4, 8))
        self.assertTrue(inside_radius(35.7, 51.4, 35.7, 51.4, 100))
        self.assertFalse(inside_radius(35.7, 51.4, 35.8, 51.5, 100))
        self.assertTrue(validate_location_sample(95, 51.4, 8))

        token = create_qr_token('e2e-game', 'main#start', 'nonce-1', 'secret', time.time() + 60)
        self.assertEqual('nonce-1', verify_qr_token(token, 'secret')['nonce'])
        self.assertIsNone(verify_qr_token(token, 'wrong-secret'))
        expired = create_qr_token('e2e-game', 'main#start', 'nonce-2', 'secret', time.time() - 1)
        self.assertIsNone(verify_qr_token(expired, 'secret'))

        event = SessionEvent.objects.create(
            session=session, event_id='event-1', event_type='complete-stage', accepted=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SessionEvent.objects.create(session=session, event_id='event-1', event_type='replay')

        hint = Hint.objects.create(
            game_version=version, stage_id='main#start', title='Hint', content='Look closer', score_cost=5,
        )
        self.assertEqual(5, hint.score_cost)
        ledger_entry = ScoreLedger.objects.create(
            session=session, event=event, reason='stage-complete', amount=100,
        )
        self.assertEqual(100, session.score_entries.aggregate(total=Sum('amount'))['total'])

        session.completed = '2026-01-01T00:10:00Z'
        session.save(update_fields=['completed'])
        session.refresh_from_db()
        self.assertIsNotNone(session.completed)
        self.assertEqual(version, session.game_version)

    def test_invalid_location_and_qr_are_rejected(self):
        self.assertTrue(validate_location_sample(91, 51.4, 8))
        self.assertFalse(inside_radius(35.7, 51.4, 35.8, 51.5, 100))
        token = create_qr_token('game', 'stage', 'nonce', 'secret', time.time() - 1)
        self.assertIsNone(verify_qr_token(token, 'secret'))
        self.assertIsNone(verify_qr_token(token + 'tampered', 'secret'))

    def test_public_access_rejects_unpublished_games_and_inactive_cities(self):
        city = City.objects.create(slug='inactive-city', name='Inactive', is_active=False)
        game = Game.objects.create(name='Draft Game', slug='draft-game', city=city)
        GameVersion.objects.create(
            game=game,
            created='2026-01-01T00:00:00Z',
            definition=json.dumps({'sequences': [{'id': 'main', 'items': [{'id': 'start'}]}]}),
        )
        self.assertEqual(404, self.client.get(reverse('public_game_detail', args=[game.slug])).status_code)
        self.assertEqual(404, self.client.get(reverse('public_city_games', args=[city.slug])).status_code)
