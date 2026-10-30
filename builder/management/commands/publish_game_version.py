from django.core.management.base import BaseCommand, CommandError

from builder.models import GameVersion


class Command(BaseCommand):
    help = 'Validate and publish a game version.'

    def add_arguments(self, parser):
        parser.add_argument('version_id', type=int)

    def handle(self, *args, **options):
        try:
            version = GameVersion.objects.get(pk=options['version_id'])
        except GameVersion.DoesNotExist as error:
            raise CommandError('Game version does not exist.') from error

        published, errors = version.publish()
        if not published:
            for error in errors:
                self.stderr.write(error)
            raise CommandError('Game version was not published.')

        self.stdout.write(self.style.SUCCESS('Game version published.'))
