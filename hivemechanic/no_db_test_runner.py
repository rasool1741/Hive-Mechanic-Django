import os

from django.apps import apps
from django.core.management.base import CommandError
from django.db import DatabaseError, connections, router, transaction
from django.test.runner import DiscoverRunner

ALLOW_ENVIRONMENT_VARIABLE = 'HIVE_ALLOW_TESTS_ON_CONFIGURED_DATABASE'

# A database that has only been migrated already holds rows in these models (measured with a plain
# `migrate`: permissions, content types and the default site). They are not data worth protecting.
CREATED_BY_MIGRATIONS = frozenset({'auth.permission', 'contenttypes.contenttype', 'sites.site'})

SHOWN_MODELS = 8


def existing_data(aliases=None):
    """Models that hold rows in the configured database(s), as [(alias, 'app_label.model', count)].

    Rows that every migrated database has (CREATED_BY_MIGRATIONS) do not count. A model whose table is
    missing holds nothing. A database that cannot be reached raises, it never looks empty.
    """
    found = []
    for alias in sorted(connections if aliases is None else aliases):
        connections[alias].ensure_connection()
        for model in apps.get_models():
            label = model._meta.label_lower
            if model._meta.proxy or not model._meta.managed or label in CREATED_BY_MIGRATIONS:
                continue
            if not router.allow_migrate_model(alias, model):
                continue
            try:
                with transaction.atomic(using=alias):  # own savepoint: a missing table must not poison a transaction
                    queryset = model._default_manager.using(alias)
                    count = queryset.count() if queryset.exists() else 0
            except DatabaseError:
                continue
            if count:
                found.append((alias, label, count))
    return found


def refusal_message(found):
    several_databases = len({alias for alias, _label, _count in found}) > 1
    listing = ', '.join(
        '%s: %d rows%s' % (label, count, ' (database %s)' % alias if several_databases else '') for alias, label, count in found[:SHOWN_MODELS]
    )
    if len(found) > SHOWN_MODELS:
        listing += ', and %d more models' % (len(found) - SHOWN_MODELS)
    return (
        'Refusing to run the tests on the configured database, which already holds data (%s).\n\n'
        'This test runner creates no test database, so the tests would run on that data, and some of them '
        '(the browser tests) erase the whole database when they finish.\n\n'
        'Use a throwaway test database instead:\n'
        '    python manage.py test --testrunner django.test.runner.DiscoverRunner <tests>\n'
        'or point DATABASES at an empty database.\n'
        'To run on this database anyway, set %s=1.' % (listing, ALLOW_ENVIRONMENT_VARIABLE)
    )


class NoDbTestRunner(DiscoverRunner):
  """ A test runner to test without database creation. It refuses to run on a database that holds data. """

  def setup_databases(self, **kwargs):
    """ Create no test database, but never run on a database that already holds data """
    found = existing_data(kwargs.get('aliases'))

    if not found:
      return None

    if os.environ.get(ALLOW_ENVIRONMENT_VARIABLE) == '1':
      self.log('WARNING: running the tests on a database that holds data, because %s=1 is set.' % ALLOW_ENVIRONMENT_VARIABLE)
      return None

    raise CommandError(refusal_message(found))

  def teardown_databases(self, old_config, **kwargs):
    """ Override the database teardown defined in parent class """
    pass
