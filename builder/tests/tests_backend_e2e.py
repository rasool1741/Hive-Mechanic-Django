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
