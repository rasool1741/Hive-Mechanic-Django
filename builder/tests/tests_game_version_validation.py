import json

from django.test import TestCase

from ..models import Game, GameVersion, InteractionCard


class GameVersionValidationTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(name='Validation Game', slug='validation-game')
        self.card = InteractionCard.objects.create(
            name='Message',
            identifier='send-message',
        )
        self.game.cards.add(self.card)

    def make_version(self, definition):
        return GameVersion.objects.create(
            game=self.game,
            created='2026-01-01T00:00:00Z',
            definition=json.dumps(definition),
        )

    def test_valid_definition_can_be_published(self):
        version = self.make_version({
            'sequences': [{
                'id': 'main',
                'items': [{'id': 'start', 'type': 'send-message'}],
            }],
            'initial-card': 'main#start',
        })

        published, errors = version.publish()

        self.assertTrue(published)
        self.assertEqual([], errors)
        version.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_PUBLISHED, version.status)
        self.assertEqual([], version.validation_errors)
        self.assertTrue(version.definition_checksum)

    def test_missing_card_prevents_publication(self):
        version = self.make_version({
            'sequences': [{
                'id': 'main',
                'items': [{'id': 'start', 'type': 'missing-card'}],
            }],
            'initial-card': 'main#start',
        })

        published, errors = version.publish()

        self.assertFalse(published)
        self.assertTrue(any('missing or disabled' in error for error in errors))
        version.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_DRAFT, version.status)
