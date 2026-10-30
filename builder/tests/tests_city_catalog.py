import json

from django.test import TestCase, RequestFactory

from ..models import City, Game, GameVersion
from ..views import public_city_catalog, public_city_games


class CityCatalogTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.city = City.objects.create(name='Tehran', slug='tehran', country='Iran')
        self.game = Game.objects.create(
            name='Published Game',
            slug='published-game',
            city=self.city,
            status=Game.STATUS_PUBLISHED,
        )
        GameVersion.objects.create(
            game=self.game,
            created='2026-01-01T00:00:00Z',
            status=GameVersion.STATUS_PUBLISHED,
            definition=json.dumps({'sequences': []}),
        )

    def test_catalog_contains_only_published_games(self):
        response = public_city_catalog(self.factory.get('/api/v1/cities'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(1, len(json.loads(response.content)['cities']))

    def test_city_games_endpoint(self):
        response = public_city_games(self.factory.get('/api/v1/cities/tehran/games'), 'tehran')
        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(['published-game'], [game['slug'] for game in payload['games']])
