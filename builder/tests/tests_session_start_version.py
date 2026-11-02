import datetime
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from integrations.models import Integration, execute_action

from ..models import Game, GameVersion, Player, Session

TWO_STAGES = json.dumps({
    'sequences': [{'id': 'main', 'items': [{'id': 'one'}, {'id': 'two'}]}],
    'initial-card': 'main#one',
})


def make_game(slug, status=Game.STATUS_DRAFT):
    return Game.objects.create(name=slug, slug=slug, status=status)


def make_version(game, days_ago, status=GameVersion.STATUS_DRAFT, published=None):
    created = timezone.now() - datetime.timedelta(days=days_ago)
    version = GameVersion.objects.create(game=game, created=created, definition=TWO_STAGES)
    GameVersion.objects.filter(pk=version.pk).update(status=status, published=published)
    version.refresh_from_db()
    return version


def publish_new(game, days_ago):
    version = make_version(game, days_ago)
    published, errors = version.publish()
    assert published, errors
    return version


class VersionForNewSessionTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(identifier='user:start-player')
        self.now = timezone.now()

    def test_never_released_legacy_game_keeps_using_latest_version(self):
        # Legacy / original Hive Mechanic games only have Draft versions.
        game = make_game('legacy-game')
        make_version(game, 3)
        latest = make_version(game, 1)
        self.assertFalse(game.is_released())
        self.assertEqual(latest, game.version_for_new_session())
        session = game.start_session(self.player)
        self.assertEqual(latest, session.game_version)
        self.assertEqual(self.player, session.player)
        self.assertIsNone(session.completed)

    def test_released_game_never_starts_on_a_newer_draft_or_testing_version(self):
        game = make_game('released-game')
        published = publish_new(game, 5)
        make_version(game, 2, status=GameVersion.STATUS_DRAFT)
        make_version(game, 1, status=GameVersion.STATUS_TESTING)
        self.assertTrue(game.is_released())
        self.assertEqual(published, game.version_for_new_session())
        self.assertEqual(published, game.start_session(self.player).game_version)

    def test_game_with_only_archived_versions_cannot_start(self):
        game = make_game('archived-only-game')
        make_version(game, 3, status=GameVersion.STATUS_ARCHIVED, published=self.now)
        make_version(game, 1, status=GameVersion.STATUS_DRAFT)
        self.assertTrue(game.is_released())
        self.assertIsNone(game.version_for_new_session())
        self.assertIsNone(game.start_session(self.player))
        self.assertEqual(0, Session.objects.count())

    def test_game_marked_published_without_published_version_cannot_start(self):
        game = make_game('marked-published-game', status=Game.STATUS_PUBLISHED)
        make_version(game, 1, status=GameVersion.STATUS_DRAFT)
        self.assertIsNone(game.start_session(self.player))
        self.assertEqual(0, Session.objects.count())

    def test_start_session_decides_from_the_locked_fresh_row_not_a_stale_instance(self):
        game = make_game('stale-game')
        make_version(game, 1)  # Draft only: a never-released Legacy game as far as this instance knows
        stale = Game.objects.get(pk=game.pk)
        Game.objects.filter(pk=game.pk).update(status=Game.STATUS_PUBLISHED)  # released meanwhile

        self.assertFalse(stale.is_released())  # the stale instance still believes it is Legacy
        self.assertIsNone(stale.start_session(self.player))  # the fresh locked row says: released, nothing published
        self.assertEqual(0, Session.objects.count())

    def test_game_without_any_version_cannot_start(self):
        game = make_game('empty-game')
        self.assertIsNone(game.start_session(self.player))
        self.assertEqual(0, Session.objects.count())


class NewPublishKeepsInFlightSessionsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user_a = user_model.objects.create_user(username='player-a', password='pw')
        self.user_b = user_model.objects.create_user(username='player-b', password='pw')
        self.player_a = Player.objects.create(identifier='user:player-a', user=self.user_a)
        self.player_b = Player.objects.create(identifier='user:player-b', user=self.user_b)

    def action(self, session, **payload):
        return self.client.post(reverse('game_action', args=[session.pk]), data=json.dumps(payload), content_type='application/json')

    def test_start_picks_new_version_and_previous_session_continues_on_old_one(self):
        game = make_game('flow-game')
        v1 = publish_new(game, 3)

        session_a = game.start_session(self.player_a)
        self.assertEqual(v1, session_a.game_version)

        v2 = publish_new(game, 1)

        # The old version is still Published only because a player is mid-game.
        v1.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_PUBLISHED, v1.status)
        self.assertEqual(v2, game.published_version())

        # A player starting now gets the NEW published version.
        session_b = game.start_session(self.player_b)
        self.assertEqual(v2, session_b.game_version)

        # The existing player is not moved: the active session still points to v1.
        self.assertEqual(session_a, game.current_active_session(self.player_a))
        session_a.refresh_from_db()
        self.assertEqual(v1, session_a.game_version)

        # ...and can keep playing on v1 through the real action endpoint.
        self.client.login(username='player-a', password='pw')
        first = self.action(session_a, event_id='a1', stage_id='main#one')
        self.assertEqual(200, first.status_code, first.content)
        self.assertEqual('main#two', first.json()['next_stage_id'])
        second = self.action(session_a, event_id='a2', stage_id='main#two')
        self.assertEqual(200, second.status_code, second.content)
        self.assertTrue(second.json()['completed'])
        session_a.refresh_from_db()
        self.assertEqual(v1, session_a.game_version)
        self.assertIsNotNone(session_a.completed)

        # The new player plays on v2 independently.
        self.client.logout()
        self.client.login(username='player-b', password='pw')
        self.assertEqual(200, self.action(session_b, event_id='b1', stage_id='main#one').status_code)
        session_b.refresh_from_db()
        self.assertEqual(v2, session_b.game_version)

        # Once the old session finished, the next publish archives v1.
        v3 = publish_new(game, 0)
        v1.refresh_from_db()
        self.assertEqual(GameVersion.STATUS_ARCHIVED, v1.status)
        self.assertEqual(v3, game.published_version())


class IntegrationStartPathTests(TestCase):
    """The two code paths in integrations/models.py that create sessions for real players."""

    def setUp(self):
        self.game = make_game('integration-game')
        self.integration = Integration.objects.create(name='Start path integration', url_slug='start-path', type='command_line', game=self.game)

        patches = [
            mock.patch.object(Session, 'process_incoming', return_value=None),
            mock.patch.object(Session, 'visited_terms', return_value=True),
            mock.patch.object(Session, 'nudge', return_value=None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def incoming(self, phone):
        self.integration.process_player_incoming('test_phone', phone, 'hello', {'message_type': 'text'})
        return Session.objects.filter(player__player_state__test_phone=phone).order_by('-started').first()

    # --- Integration.process_player_incoming -------------------------------------

    def test_incoming_message_starts_on_published_version_not_newer_draft(self):
        published = publish_new(self.game, 5)
        make_version(self.game, 1, status=GameVersion.STATUS_DRAFT)
        session = self.incoming('+10000000001')
        self.assertEqual(published, session.game_version)

    def test_incoming_after_new_publish_new_player_gets_new_version_and_old_player_resumes(self):
        v1 = publish_new(self.game, 5)
        old_session = self.incoming('+10000000002')
        self.assertEqual(v1, old_session.game_version)

        v2 = publish_new(self.game, 1)

        new_session = self.incoming('+10000000003')
        self.assertEqual(v2, new_session.game_version)

        count_before = Session.objects.count()
        resumed = self.incoming('+10000000002')
        self.assertEqual(old_session, resumed)
        self.assertEqual(v1, resumed.game_version)
        self.assertEqual(count_before, Session.objects.count())

    def test_incoming_message_for_never_released_legacy_game_uses_latest_version(self):
        make_version(self.game, 3)
        latest = make_version(self.game, 1)
        self.assertEqual(latest, self.incoming('+10000000004').game_version)

    def test_incoming_message_without_startable_version_creates_no_session(self):
        make_version(self.game, 3, status=GameVersion.STATUS_ARCHIVED, published=timezone.now())
        make_version(self.game, 1, status=GameVersion.STATUS_DRAFT)
        self.assertIsNone(self.incoming('+10000000005'))
        self.assertEqual(0, Session.objects.count())

    # --- launch-session action ---------------------------------------------------

    def launch(self, target_slug, player_identifier):
        other_game = make_game('launcher-game')
        launcher_player = Player.objects.create(identifier='user:launcher')
        launcher_session = Session.objects.create(
            game_version=make_version(other_game, 1), player=launcher_player, started=timezone.now())
        return execute_action(self.integration, launcher_session, {
            'type': 'launch-session', 'activity': target_slug, 'player': player_identifier})

    def test_launch_session_uses_published_version_and_replaces_the_active_session(self):
        v1 = publish_new(self.game, 5)
        target = Player.objects.create(identifier='user:launch-target')
        old_session = Session.objects.create(game_version=v1, player=target, started=timezone.now())
        v2 = publish_new(self.game, 3)
        make_version(self.game, 1, status=GameVersion.STATUS_DRAFT)

        self.assertTrue(self.launch(self.game.slug, 'user:launch-target'))

        old_session.refresh_from_db()
        self.assertIsNotNone(old_session.completed)
        new_session = Session.objects.filter(player=target, completed=None).get()
        self.assertEqual(v2, new_session.game_version)

    def test_launch_session_without_startable_version_leaves_active_session_untouched(self):
        legacy_version = make_version(self.game, 3, status=GameVersion.STATUS_ARCHIVED, published=timezone.now())
        target = Player.objects.create(identifier='user:launch-target-2')
        active = Session.objects.create(game_version=legacy_version, player=target, started=timezone.now())

        self.assertFalse(self.launch(self.game.slug, 'user:launch-target-2'))

        active.refresh_from_db()
        self.assertIsNone(active.completed)
        self.assertEqual(1, Session.objects.filter(player=target).count())

    def test_launch_session_for_never_released_legacy_game_uses_latest_version(self):
        make_version(self.game, 3)
        latest = make_version(self.game, 1)
        Player.objects.create(identifier='user:launch-target-3')

        self.assertTrue(self.launch(self.game.slug, 'user:launch-target-3'))
        self.assertEqual(latest, Session.objects.get(player__identifier='user:launch-target-3').game_version)
