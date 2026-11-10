import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.db import OperationalError, connections
from django.db.models.query import QuerySet
from django.test import TestCase

from builder.models import City, Game

from . import no_db_test_runner
from .no_db_test_runner import ALLOW_ENVIRONMENT_VARIABLE, NoDbTestRunner, existing_data, refusal_message

OFF = {ALLOW_ENVIRONMENT_VARIABLE: ''}


class ExistingDataTests(TestCase):
    """NoDbTestRunner runs tests on the configured database, so it must notice data worth protecting."""

    def test_a_freshly_migrated_database_counts_as_empty(self):
        self.assertEqual([], existing_data())

    def test_the_allowance_matches_what_migrations_really_create(self):
        # If a future migration seeds other rows, this fails and tells you to extend CREATED_BY_MIGRATIONS,
        # instead of the runner silently refusing every freshly migrated database.
        with mock.patch.object(no_db_test_runner, 'CREATED_BY_MIGRATIONS', frozenset()):
            labels = {label for _alias, label, _count in existing_data()}
        self.assertEqual(set(no_db_test_runner.CREATED_BY_MIGRATIONS), labels)

    def test_user_data_is_reported_with_counts(self):
        get_user_model().objects.create_user(username='someone', password='pw')
        get_user_model().objects.create_user(username='someone-else', password='pw')
        city = City.objects.create(slug='guard-city', name='Guard')
        Game.objects.create(name='Guard game', slug='guard-game', city=city)
        found = {label: count for _alias, label, count in existing_data()}
        self.assertEqual(2, found['auth.user'])
        self.assertEqual(1, found['builder.city'])
        self.assertEqual(1, found['builder.game'])
        self.assertTrue(all(alias == 'default' for alias, _label, _count in existing_data()))

    def test_one_row_in_one_model_is_enough(self):
        City.objects.create(slug='lonely-city', name='Lonely')
        self.assertEqual([('default', 'builder.city', 1)], existing_data())

    def test_only_the_requested_aliases_are_checked(self):
        City.objects.create(slug='aliases-city', name='Aliases')
        self.assertEqual([], existing_data(aliases=set()))  # tests that need no database protect nothing
        self.assertEqual(1, len(existing_data(aliases={'default'})))

    def test_a_missing_table_means_no_data_not_an_error(self):
        with mock.patch.object(QuerySet, 'exists', side_effect=OperationalError('no such table')):
            self.assertEqual([], existing_data())

    def test_a_database_that_cannot_be_reached_is_not_mistaken_for_an_empty_one(self):
        with mock.patch.object(connections['default'], 'ensure_connection', side_effect=OperationalError('could not connect')):
            with self.assertRaises(OperationalError):
                existing_data()


class SetupDatabasesTests(TestCase):
    def test_runs_on_an_empty_database_and_creates_nothing(self):
        with mock.patch.dict(os.environ, OFF):
            self.assertIsNone(NoDbTestRunner().setup_databases(aliases={'default'}))
            self.assertIsNone(NoDbTestRunner().setup_databases())

    def test_refuses_a_database_that_holds_data_and_leaves_it_untouched(self):
        get_user_model().objects.create_user(username='keep-me', password='pw')
        City.objects.create(slug='keep-city', name='Keep')
        with mock.patch.dict(os.environ, OFF):
            with self.assertRaises(CommandError) as raised:
                NoDbTestRunner().setup_databases(aliases={'default'})
        message = str(raised.exception)
        for expected in ('already holds data', 'auth.user: 1 rows', 'builder.city: 1 rows', 'django.test.runner.DiscoverRunner', ALLOW_ENVIRONMENT_VARIABLE):
            self.assertIn(expected, message)
        self.assertTrue(get_user_model().objects.filter(username='keep-me').exists())
        self.assertTrue(City.objects.filter(slug='keep-city').exists())

    def test_the_explicit_override_allows_it_and_says_so(self):
        City.objects.create(slug='override-city', name='Override')
        runner = NoDbTestRunner()
        with mock.patch.dict(os.environ, {ALLOW_ENVIRONMENT_VARIABLE: '1'}), mock.patch.object(NoDbTestRunner, 'log') as log:
            self.assertIsNone(runner.setup_databases(aliases={'default'}))
        self.assertIn('WARNING', log.call_args[0][0])
        self.assertIn(ALLOW_ENVIRONMENT_VARIABLE, log.call_args[0][0])

    def test_only_the_value_1_is_an_override(self):
        City.objects.create(slug='strict-city', name='Strict')
        for value in ('', '0', 'true', 'yes', 'True', ' 1', '11'):
            with mock.patch.dict(os.environ, {ALLOW_ENVIRONMENT_VARIABLE: value}):
                with self.assertRaises(CommandError, msg=repr(value)):
                    NoDbTestRunner().setup_databases(aliases={'default'})

    def test_teardown_does_nothing(self):
        self.assertIsNone(NoDbTestRunner().teardown_databases(old_config=None))

    def test_refusal_message_lists_a_few_models_and_counts_the_rest(self):
        found = [('default', 'app.model%d' % index, index + 1) for index in range(12)]
        message = refusal_message(found)
        self.assertIn('app.model0: 1 rows', message)
        self.assertIn('app.model7: 8 rows', message)
        self.assertNotIn('app.model8', message)
        self.assertIn('and 4 more models', message)
        self.assertNotIn('more models', refusal_message(found[:3]))
        self.assertNotIn('(database', message)  # one database: no need to name it

    def test_refusal_message_names_the_database_only_when_there_are_several(self):
        message = refusal_message([('default', 'builder.game', 3), ('archive', 'builder.city', 1)])
        self.assertIn('builder.game: 3 rows (database default)', message)
        self.assertIn('builder.city: 1 rows (database archive)', message)
