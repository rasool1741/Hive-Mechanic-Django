import json
import time
import base64

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from ..models import City, Game, GameVersion, Hint, Player, ScoreLedger, Session, SessionEvent, SessionMediaSubmission
from filer.models import filemodels
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

    def test_graph_tools_hint_timer_quiz_and_text_are_server_validated(self):
        user = get_user_model().objects.create_user(username='tools-player', password='secret')
        city = City.objects.create(slug='tools-city', name='Tools City', center_latitude=35.7, center_longitude=51.4)
        game = Game.objects.create(name='Tools Game', slug='tools-game', city=city)
        definition = {'sequences': [{'id': 'legacy', 'items': [{'id': 'fallback'}]}], 'initial-card': 'legacy#fallback', 'graph': {
            'nodes': [{'id': 'tool-start', 'tools': [
                {'type': 'hint', 'settings': {'content': 'Look behind the sign', 'score_cost': 5}},
                {'type': 'timer', 'settings': {'minimum_seconds': 5}},
                {'type': 'quiz', 'settings': {'answer': '42', 'score': 25}},
                {'type': 'text_input', 'settings': {'exact': 'urban', 'max_length': 20}},
            ]}, {'id': 'tool-end'}],
            'edges': [{'id': 'tool-edge', 'source': 'tool-start', 'target': 'tool-end'}], 'start': 'tool-start', 'end': 'tool-end'}}
        version = GameVersion.objects.create(game=game, created='2026-01-01T00:00:00Z', definition=json.dumps(definition))
        self.assertTrue(version.publish()[0])
        player = Player.objects.create(identifier='user:tools-player', user=user)
        session = Session.objects.create(player=player, game_version=version, started='2026-01-01T00:00:00Z')
        self.client.login(username='tools-player', password='secret')
        endpoint = reverse('game_action', args=[session.pk])

        hint = self.client.post(endpoint, data=json.dumps({'event_id': 'hint-1', 'stage_id': 'tool-start', 'operation': 'hint'}), content_type='application/json')
        self.assertEqual(200, hint.status_code)
        self.assertEqual('Look behind the sign', hint.json()['content'])
        self.assertNotIn('42', json.dumps(hint.json()))
        replay = self.client.post(endpoint, data=json.dumps({'event_id': 'hint-1', 'stage_id': 'tool-start', 'operation': 'hint'}), content_type='application/json')
        self.assertEqual(hint.json(), replay.json())
        early = self.client.post(endpoint, data=json.dumps({'event_id': 'tool-early', 'stage_id': 'tool-start', 'answer': '42', 'text': 'urban'}), content_type='application/json')
        self.assertEqual(409, early.status_code)

        session.refresh_from_db()
        session.session_state['graph_timer_started_at:tool-start'] = time.time() - 10
        session.save(update_fields=['session_state'])
        wrong = self.client.post(endpoint, data=json.dumps({'event_id': 'tool-wrong', 'stage_id': 'tool-start', 'answer': 'wrong', 'text': 'urban'}), content_type='application/json')
        self.assertEqual(403, wrong.status_code)
        valid = self.client.post(endpoint, data=json.dumps({'event_id': 'tool-valid', 'stage_id': 'tool-start', 'answer': '42', 'text': 'urban', 'score': 9999}), content_type='application/json')
        self.assertEqual(200, valid.status_code)
        self.assertEqual(100, valid.json()['score'])
        self.assertEqual(-5, ScoreLedger.objects.filter(session=session, reason='hint-cost').first().amount)

    def test_graph_media_reference_is_server_bound_and_type_checked(self):
        user = get_user_model().objects.create_user(username='media-player', password='secret')
        city = City.objects.create(slug='media-city', name='Media City')
        game = Game.objects.create(name='Media Game', slug='media-game', city=city)
        media_file = filemodels.File.objects.create(
            file=ContentFile(b'fake-png-content', name='server-image.png'),
            original_filename='server-image.png', name='server-image', mime_type='image/png',
            has_all_mandatory_data=True, is_public=False,
        )
        definition = {'sequences': [{'id': 'legacy', 'items': [{'id': 'fallback'}]}], 'initial-card': 'legacy#fallback', 'graph': {
            'nodes': [{'id': 'media-start', 'tools': [{'type': 'image', 'settings': {'file_id': media_file.pk, 'media_type': 'image'}}]}, {'id': 'media-end'}],
            'edges': [{'id': 'media-edge', 'source': 'media-start', 'target': 'media-end'}], 'start': 'media-start', 'end': 'media-end'}}
        version = GameVersion.objects.create(game=game, created='2026-01-01T00:00:00Z', definition=json.dumps(definition))
        self.assertTrue(version.publish()[0])
        player = Player.objects.create(identifier='user:media-player', user=user)
        session = Session.objects.create(player=player, game_version=version, started='2026-01-01T00:00:00Z')
        self.client.login(username='media-player', password='secret')
        endpoint = reverse('game_action', args=[session.pk])
        response = self.client.post(endpoint, data=json.dumps({'event_id': 'media-1', 'stage_id': 'media-start', 'operation': 'media'}), content_type='application/json')
        self.assertEqual(200, response.status_code)
        self.assertEqual(media_file.pk, response.json()['file_id'])
        self.assertNotIn('fake-png-content', response.content.decode())
        self.assertEqual(409, self.client.post(endpoint, data=json.dumps({'event_id': 'media-2', 'stage_id': 'media-end', 'operation': 'media'}), content_type='application/json').status_code)

    def test_private_player_image_upload_and_required_submission(self):
        user = get_user_model().objects.create_user(username='uploader', password='secret')
        other = get_user_model().objects.create_user(username='other-uploader', password='secret')
        city = City.objects.create(slug='upload-city', name='Upload City')
        game = Game.objects.create(name='Upload Game', slug='upload-game', city=city)
        definition = {'sequences': [{'id': 'legacy', 'items': [{'id': 'fallback'}]}], 'initial-card': 'legacy#fallback', 'graph': {
            'nodes': [{'id': 'upload-start', 'tools': [{'type': 'image', 'settings': {'allow_upload': True, 'required': True, 'media_type': 'image'}}]}, {'id': 'upload-end'}],
            'edges': [{'id': 'upload-edge', 'source': 'upload-start', 'target': 'upload-end'}], 'start': 'upload-start', 'end': 'upload-end'}}
        version = GameVersion.objects.create(game=game, created='2026-01-01T00:00:00Z', definition=json.dumps(definition))
        self.assertTrue(version.publish()[0])
        player = Player.objects.create(identifier='user:uploader', user=user)
        session = Session.objects.create(player=player, game_version=version, started='2026-01-01T00:00:00Z')
        endpoint = reverse('player_media_upload', args=[session.pk])
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        self.client.login(username='uploader', password='secret')
        empty = self.client.post(endpoint, {'event_id': 'empty', 'file': SimpleUploadedFile('x.png', b'', content_type='image/png')})
        self.assertEqual(415, empty.status_code)
        invalid = self.client.post(endpoint, {'event_id': 'html', 'file': SimpleUploadedFile('x.png', b'<html>bad</html>', content_type='image/png')})
        self.assertEqual(415, invalid.status_code)
        oversized = self.client.post(endpoint, {'event_id': 'large', 'file': SimpleUploadedFile('x.png', b'0' * (10 * 1024 * 1024 + 1), content_type='image/png')})
        self.assertEqual(413, oversized.status_code)
        uploaded = self.client.post(endpoint, {'event_id': 'upload-1', 'file': SimpleUploadedFile('ignored-name.png', image, content_type='text/html')})
        self.assertEqual(200, uploaded.status_code)
        submission_id = uploaded.json()['submission_id']
        submission = SessionMediaSubmission.objects.get(pk=submission_id)
        self.assertEqual(session, submission.session)
        self.assertEqual('upload-start', submission.node_id)
        self.assertTrue(submission.file.name.startswith('submissions/'))
        action = reverse('game_action', args=[session.pk])
        completed = self.client.post(action, data=json.dumps({'event_id': 'complete-upload', 'stage_id': 'upload-start', 'submission_id': submission_id}), content_type='application/json')
        self.assertEqual(200, completed.status_code)
        self.assertTrue(completed.json()['accepted'])
        self.client.logout()
        self.client.login(username='other-uploader', password='secret')
        self.assertEqual(404, self.client.post(endpoint, {'event_id': 'other', 'file': SimpleUploadedFile('x.png', image, content_type='image/png')}).status_code)

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
