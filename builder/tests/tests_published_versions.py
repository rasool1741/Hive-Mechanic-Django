import datetime
import json
from unittest import mock

from django.db.models.query import QuerySet
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ..models import City, Game, GameVersion, Player, Session

DEFINITION = json.dumps({'sequences': [{'id': 'main', 'items': [{'id': 'one'}]}], 'initial-card': 'main#one'})
INVALID_DEFINITION = json.dumps({'sequences': []})


def make_game(slug, city=None, status=Game.STATUS_PUBLISHED):
    return Game.objects.create(name=slug, slug=slug, city=city, status=status)


def make_version(game, created, status=GameVersion.STATUS_DRAFT, published=None, definition=DEFINITION):
    version = GameVersion.objects.create(game=game, created=created, definition=definition)
    GameVersion.objects.filter(pk=version.pk).update(status=status, published=published)
    version.refresh_from_db()
    return version


class PublishedVersionRuleTests(TestCase):
    def setUp(self):
        self.game = make_game('rule-game')
        self.t1 = timezone.now() - datetime.timedelta(days=3)
        self.t2 = timezone.now() - datetime.timedelta(days=2)
        self.t3 = timezone.now() - datetime.timedelta(days=1)

    def test_no_published_version_returns_none(self):
        make_version(self.game, self.t1, status=GameVersion.STATUS_DRAFT)
        make_version(self.game, self.t2, status=GameVersion.STATUS_ARCHIVED, published=self.t2)
        self.assertIsNone(self.game.published_version())

    def test_most_recently_published_wins_even_if_created_earlier(self):
        older_created_newer_published = make_version(self.game, self.t1, GameVersion.STATUS_PUBLISHED, published=self.t3)
        make_version(self.game, self.t2, GameVersion.STATUS_PUBLISHED, published=self.t2)
        self.assertEqual(older_created_newer_published, self.game.published_version())

    def test_tie_on_published_uses_newest_created_then_pk(self):
        make_version(self.game, self.t1, GameVersion.STATUS_PUBLISHED, published=self.t3)
        newer_created = make_version(self.game, self.t2, GameVersion.STATUS_PUBLISHED, published=self.t3)
        self.assertEqual(newer_created, self.game.published_version())
        same_created_higher_pk = make_version(self.game, self.t2, GameVersion.STATUS_PUBLISHED, published=self.t3)
        self.assertEqual(same_created_higher_pk, self.game.published_version())

    def test_published_without_timestamp_sorts_last(self):
        with_timestamp = make_version(self.game, self.t1, GameVersion.STATUS_PUBLISHED, published=self.t1)
        make_version(self.game, self.t3, GameVersion.STATUS_PUBLISHED, published=None)
        self.assertEqual(with_timestamp, self.game.published_version())

    def test_other_games_versions_are_ignored(self):
        other = make_game('rule-other')
        make_version(other, self.t3, GameVersion.STATUS_PUBLISHED, published=self.t3)
        self.assertIsNone(self.game.published_version())


class PublicGameDetailTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def detail(self, slug):
        return self.client.get(reverse('public_game_detail', args=[slug]))

    def test_multiple_published_rows_do_not_crash(self):
        # Reproduces MultipleObjectsReturned: status can be set directly in the admin.
        game = make_game('multi-game', city=City.objects.create(slug='multi-city', name='Multi'))
        make_version(game, self.now - datetime.timedelta(days=2), GameVersion.STATUS_PUBLISHED, published=self.now - datetime.timedelta(days=2))
        make_version(game, self.now - datetime.timedelta(days=1), GameVersion.STATUS_PUBLISHED, published=self.now - datetime.timedelta(days=1))
        response = self.detail('multi-game')
        self.assertEqual(200, response.status_code)
        self.assertEqual('multi-game', response.json()['slug'])

    def test_inactive_city_is_rejected_for_published_game(self):
        city = City.objects.create(slug='off-city', name='Off', is_active=False)
        game = make_game('off-game', city=city)
        make_version(game, self.now, GameVersion.STATUS_PUBLISHED, published=self.now)
        self.assertEqual(404, self.detail('off-game').status_code)
        self.assertEqual(404, self.client.get(reverse('public_city_games', args=[city.slug])).status_code)

    def test_active_city_is_accepted(self):
        city = City.objects.create(slug='on-city', name='On', is_active=True)
        game = make_game('on-game', city=city)
        make_version(game, self.now, GameVersion.STATUS_PUBLISHED, published=self.now)
        response = self.detail('on-game')
        self.assertEqual(200, response.status_code)
        self.assertEqual('on-city', response.json()['city'])

    def test_game_without_city_is_still_accepted(self):
        game = make_game('no-city-game')
        make_version(game, self.now, GameVersion.STATUS_PUBLISHED, published=self.now)
        response = self.detail('no-city-game')
        self.assertEqual(200, response.status_code)
        self.assertIsNone(response.json()['city'])

    def test_game_without_published_version_is_rejected(self):
        game = make_game('draft-version-game')
        make_version(game, self.now, GameVersion.STATUS_DRAFT)
        self.assertEqual(404, self.detail('draft-version-game').status_code)

    def test_unpublished_or_archived_game_is_rejected_even_with_published_version(self):
        draft_game = make_game('draft-game', status=Game.STATUS_DRAFT)
        make_version(draft_game, self.now, GameVersion.STATUS_PUBLISHED, published=self.now)
        self.assertEqual(404, self.detail('draft-game').status_code)
        archived_game = make_game('archived-game')
        make_version(archived_game, self.now, GameVersion.STATUS_PUBLISHED, published=self.now)
        Game.objects.filter(pk=archived_game.pk).update(archived=self.now)
        self.assertEqual(404, self.detail('archived-game').status_code)

    def test_unknown_game_is_rejected(self):
        self.assertEqual(404, self.detail('does-not-exist').status_code)

    def test_response_keys_are_unchanged(self):
        game = make_game('keys-game')
        make_version(game, self.now, GameVersion.STATUS_PUBLISHED, published=self.now)
        self.assertEqual(
            ['city', 'description', 'difficulty', 'estimated_minutes', 'is_team_game', 'name', 'slug'],
            sorted(self.detail('keys-game').json().keys()),
        )

    def test_catalog_and_city_games_count_a_game_once_with_multiple_published(self):
        city = City.objects.create(slug='count-city', name='Count')
        game = make_game('count-game', city=city)
        make_version(game, self.now - datetime.timedelta(days=2), GameVersion.STATUS_PUBLISHED, published=self.now - datetime.timedelta(days=2))
        make_version(game, self.now - datetime.timedelta(days=1), GameVersion.STATUS_PUBLISHED, published=self.now - datetime.timedelta(days=1))
        catalog = self.client.get(reverse('public_city_catalog')).json()['cities']
        self.assertEqual([1], [item['games_count'] for item in catalog if item['slug'] == 'count-city'])
        games = self.client.get(reverse('public_city_games', args=[city.slug])).json()['games']
        self.assertEqual(['count-game'], [item['slug'] for item in games])


class PublishSupersedesPreviousVersionTests(TestCase):
    def setUp(self):
        self.game = make_game('publish-game')
        self.now = timezone.now()

    def new_draft(self, days_ago, definition=DEFINITION):
        return make_version(self.game, self.now - datetime.timedelta(days=days_ago), definition=definition)

    def test_publish_archives_previous_published_version(self):
        first = self.new_draft(3)
        self.assertTrue(first.publish()[0])
        second = self.new_draft(2)
        self.assertTrue(second.publish()[0])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_ARCHIVED, first.status)
        self.assertEqual(GameVersion.STATUS_PUBLISHED, second.status)
        self.assertEqual(second, self.game.published_version())
        self.assertEqual(1, self.game.versions.filter(status=GameVersion.STATUS_PUBLISHED).count())

    def test_previous_version_with_active_session_stays_published_until_it_finishes(self):
        first = self.new_draft(3)
        self.assertTrue(first.publish()[0])
        player = Player.objects.create(identifier='user:supersede-player')
        session = Session.objects.create(player=player, game_version=first, started=self.now)

        second = self.new_draft(2)
        self.assertTrue(second.publish()[0])
        first.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_PUBLISHED, first.status)  # players can finish
        self.assertEqual(second, self.game.published_version())  # new sessions use the new one

        session.completed = self.now
        session.save(update_fields=['completed'])
        third = self.new_draft(1)
        self.assertTrue(third.publish()[0])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_ARCHIVED, first.status)
        self.assertEqual(GameVersion.STATUS_ARCHIVED, second.status)
        self.assertEqual(third, self.game.published_version())

    def test_failed_publish_does_not_archive_previous_version(self):
        first = self.new_draft(3)
        self.assertTrue(first.publish()[0])
        broken = self.new_draft(2, definition=INVALID_DEFINITION)
        published, errors = broken.publish()
        self.assertFalse(published)
        self.assertTrue(errors)
        first.refresh_from_db()
        broken.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_PUBLISHED, first.status)
        self.assertEqual(GameVersion.STATUS_DRAFT, broken.status)
        self.assertEqual(first, self.game.published_version())

    def test_republishing_the_same_version_keeps_it_published(self):
        version = self.new_draft(2)
        self.assertTrue(version.publish()[0])
        self.assertTrue(version.publish()[0])
        version.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_PUBLISHED, version.status)
        self.assertEqual(version, self.game.published_version())

    def test_publish_does_not_touch_other_games(self):
        other_game = make_game('publish-other-game')
        other = make_version(other_game, self.now - datetime.timedelta(days=5), GameVersion.STATUS_PUBLISHED, published=self.now)
        self.assertTrue(self.new_draft(1).publish()[0])
        other.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_PUBLISHED, other.status)

    def test_testing_and_draft_versions_are_not_archived(self):
        testing = make_version(self.game, self.now - datetime.timedelta(days=4), GameVersion.STATUS_TESTING)
        draft = self.new_draft(3)
        self.assertTrue(self.new_draft(1).publish()[0])
        testing.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_TESTING, testing.status)
        self.assertEqual(GameVersion.STATUS_DRAFT, draft.status)


class RollbackByRepublishingTests(TestCase):
    """Publishing an older version again is a rollback."""

    def setUp(self):
        self.game = make_game('rollback-game')
        self.now = timezone.now()
        self.v1 = make_version(self.game, self.now - datetime.timedelta(days=3))
        self.assertTrue(self.v1.publish()[0])
        self.v2 = make_version(self.game, self.now - datetime.timedelta(days=2))
        self.assertTrue(self.v2.publish()[0])
        self.v1.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_ARCHIVED, self.v1.status)
        self.first_published_at = self.v1.published

    def test_republishing_an_archived_older_version_makes_it_current(self):
        self.assertTrue(self.v1.publish()[0])
        self.v1.refresh_from_db()
        self.v2.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_PUBLISHED, self.v1.status)
        self.assertEqual(GameVersion.STATUS_ARCHIVED, self.v2.status)
        self.assertEqual(self.v1, self.game.published_version())
        self.assertGreater(self.v1.published, self.first_published_at)  # timestamp refreshed
        player = Player.objects.create(identifier='user:rollback-player')
        self.assertEqual(self.v1, self.game.start_session(player).game_version)

    def test_rollback_keeps_the_replaced_version_while_its_session_is_unfinished(self):
        player = Player.objects.create(identifier='user:rollback-old-player')
        in_flight = Session.objects.create(player=player, game_version=self.v2, started=self.now)
        self.assertTrue(self.v1.publish()[0])
        self.v2.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_PUBLISHED, self.v2.status)  # players can finish
        self.assertEqual(self.v1, self.game.published_version())  # but new sessions use v1
        newcomer = Player.objects.create(identifier='user:rollback-new-player')
        self.assertEqual(self.v1, self.game.start_session(newcomer).game_version)
        self.assertEqual(in_flight, self.game.current_active_session(player))

    def test_republishing_an_older_version_with_an_invalid_definition_changes_nothing(self):
        GameVersion.objects.filter(pk=self.v1.pk).update(definition=INVALID_DEFINITION)
        self.v1.refresh_from_db()
        published, errors = self.v1.publish()
        self.assertFalse(published)
        self.assertTrue(errors)
        self.v1.refresh_from_db()
        self.v2.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_ARCHIVED, self.v1.status)
        self.assertEqual(GameVersion.STATUS_PUBLISHED, self.v2.status)
        self.assertEqual(self.v2, self.game.published_version())


class GameRowLockTests(TestCase):
    """publish() and start_session() must lock the game row (SELECT ... FOR UPDATE).

    SQLite ignores row locks, so this checks that the lock is requested on Game.
    The real concurrent behaviour is verified separately on PostgreSQL.
    """

    def locked_models(self, action):
        requested = []
        original = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            requested.append(queryset.model)
            return original(queryset, *args, **kwargs)

        with mock.patch.object(QuerySet, 'select_for_update', spy):
            action()

        return requested

    def test_publish_locks_the_game_row(self):
        game = make_game('lock-publish-game')
        version = make_version(game, timezone.now() - datetime.timedelta(days=1))
        self.assertIn(Game, self.locked_models(version.publish))

    def test_start_session_locks_the_game_row(self):
        game = make_game('lock-start-game')
        version = make_version(game, timezone.now() - datetime.timedelta(days=1))
        version.publish()
        player = Player.objects.create(identifier='user:lock-player')
        self.assertIn(Game, self.locked_models(lambda: game.start_session(player)))

    def test_failed_publish_does_not_take_the_lock(self):
        game = make_game('lock-invalid-game')
        version = make_version(game, timezone.now() - datetime.timedelta(days=1), definition=INVALID_DEFINITION)
        self.assertEqual([], self.locked_models(version.publish))
